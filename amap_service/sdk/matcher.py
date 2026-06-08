"""DB-backed map matching: GPS track → ordered road-link sequence.

Direction-aware + connectivity-preferring greedy matcher. Reads road_link_coord geometry only.

For each GPS point it picks, among links within tolerance, a link whose local direction agrees
with the GPS travel direction (so on a divided road / 上下线分离 it matches the correct one-way
link instead of the opposite carriageway), preferring to stay on the current link or move to a
link connected to it (shared endpoint) — which keeps the matched sequence a coherent, connected
path. A link is flagged `reverse` only when it was matched against its stored direction with no
same-direction alternative (a genuine two-way / single-carriageway reversal).
"""
from dataclasses import dataclass, field

from sqlalchemy import Engine, select

from amap_service.db.schema import road_link, road_link_coord
from amap_service.sdk import geometry

# Rough degrees-per-meter for bbox expansion (latitude; longitude is tighter but
# over-expanding the candidate box is harmless — only correctness of the filter matters).
_DEG_PER_M = 1.0 / 111320.0

# Soft distance penalty (meters) by roadclass — buses run on surface ordinary roads, so
# deprioritise highways (0 高速公路) and city expressways / elevated roads (6 主街/城市快速道).
# Applied only to the ranking cost, so a nearby surface road wins over an overlapping elevated
# one, but a genuinely-on-a-major-road link is still chosen when it's the only candidate.
_DEFAULT_ROADCLASS_PENALTY_M = {0: 60.0, 6: 25.0}


@dataclass
class MatchedLink:
    link_id: int
    gps_coords: list = field(default_factory=list)  # GPS points assigned to this link, in order
    reverse: bool = False                            # traversed against stored coordinate order


class LinkMatcher:
    def __init__(self, engine: Engine, tolerance_m: float = 30.0,
                 reverse_angle_deg: float = 90.0, connect_gap_m: float = 8.0,
                 detour_gap_m: float = 80.0, roadclass_penalty: dict = None,
                 hysteresis_m: float = 15.0, track_fit_m: float = 25.0):
        self.engine = engine
        self.tolerance_m = tolerance_m
        self.reverse_angle_deg = reverse_angle_deg   # bearing diff above this = opposite direction
        self.connect_gap_m = connect_gap_m           # endpoints closer than this = connected links
        self.detour_gap_m = detour_gap_m             # drop a link if keeping it detours more than this
        self.hysteresis_m = hysteresis_m             # keep current/connected link only if within this of best
        self.track_fit_m = track_fit_m               # drop a segment whose geometry strays this far off the track
        self.roadclass_penalty = (
            _DEFAULT_ROADCLASS_PENALTY_M if roadclass_penalty is None else roadclass_penalty
        )

    def match_track(self, coords: list[tuple[float, float]]) -> list[MatchedLink]:
        if not coords:
            return []
        geoms = self._load_candidate_geometries(coords)
        if not geoms:
            return []
        endpoints = {lid: (poly[0], poly[-1]) for lid, poly in geoms.items() if len(poly) >= 2}
        roadclass = self._load_roadclass(geoms.keys())

        def cost(c):  # raw distance plus a soft roadclass penalty (highways/elevated deprioritised)
            return c[1] + self.roadclass_penalty.get(roadclass.get(c[0]), 0.0)

        result: list[MatchedLink] = []
        opposed_votes: list[list[bool]] = []
        current = None
        for i, point in enumerate(coords):
            travel = self._gps_bearing(coords, i)
            candidates = []  # (link_id, dist, angle_diff)
            for link_id, polyline in geoms.items():
                if len(polyline) < 2:
                    continue
                dist, seg_bearing = self._nearest_segment(point, polyline)
                if dist <= self.tolerance_m:
                    candidates.append((link_id, dist, geometry.angle_diff(travel, seg_bearing)))
            if not candidates:
                continue  # drop points beyond tolerance

            # prefer same-direction links; fall back to all only if none align (genuine reverse)
            aligned = [c for c in candidates if c[2] <= self.reverse_angle_deg] or candidates
            best = min(aligned, key=cost)
            best_cost = cost(best)
            chosen = None
            if current is not None:
                # stay on the current link, but only while it is still within hysteresis of the
                # best option — so a clearly better (closer / lower-class) road wins instead of
                # the matcher sticking to e.g. an elevated road it drifted onto.
                cur_cands = [c for c in aligned if c[0] == current]
                if cur_cands and cost(min(cur_cands, key=cost)) <= best_cost + self.hysteresis_m:
                    chosen = min(cur_cands, key=cost)
                if chosen is None:
                    conn = [c for c in aligned if self._connected(endpoints, current, c[0])]
                    if conn and cost(min(conn, key=cost)) <= best_cost + self.hysteresis_m:
                        chosen = min(conn, key=cost)
            if chosen is None:
                chosen = best                                        # cheapest (dist + roadclass)

            opposed = chosen[2] > self.reverse_angle_deg
            # Extend the current run only if it is the same link AND the same travel direction.
            # If the bus doubles back along the same (undivided) link, the direction flips, so we
            # start a NEW segment — the link then legitimately appears twice (out + back).
            if result and result[-1].link_id == chosen[0] and opposed_votes[-1][-1] == opposed:
                result[-1].gps_coords.append(point)
                opposed_votes[-1].append(opposed)
            else:
                result.append(MatchedLink(chosen[0], [point]))
                opposed_votes.append([opposed])
            current = chosen[0]

        for matched, votes in zip(result, opposed_votes):
            matched.reverse = sum(votes) > len(votes) / 2
        result = self._prune_detours(result, geoms)
        return self._prune_offtrack(result, geoms, coords)

    def _prune_detours(self, matched: list, geoms: dict) -> list:
        """Drop a spurious "island" link whose insertion detours far more than connecting its
        neighbours directly (e.g. a long through-road caught by a single mid-link GPS point).

        For interior link i, if seam(i-1→i)+seam(i→i+1) exceeds seam(i-1→i+1) by more than
        detour_gap_m, link i is an off-route island — remove it. Iterated until stable.
        """
        def ends(ml):
            poly = geoms.get(ml.link_id, [])
            if len(poly) < 2:
                return None, None
            return (poly[-1], poly[0]) if ml.reverse else (poly[0], poly[-1])  # (start, end)

        items = list(matched)
        changed = True
        while changed and len(items) >= 3:
            changed = False
            for i in range(1, len(items) - 1):
                _, e_prev = ends(items[i - 1])
                s_cur, e_cur = ends(items[i])
                s_next, _ = ends(items[i + 1])
                if None in (e_prev, s_cur, e_cur, s_next):
                    continue
                with_cur = geometry.haversine(e_prev, s_cur) + geometry.haversine(e_cur, s_next)
                without = geometry.haversine(e_prev, s_next)
                if with_cur - without > self.detour_gap_m:
                    del items[i]
                    changed = True
                    break
        return items

    @staticmethod
    def _gps_bearing(coords, i) -> float:
        n = len(coords)
        if n < 2:
            return 0.0
        a = coords[i - 1] if i > 0 else coords[i]
        b = coords[i + 1] if i < n - 1 else coords[i]
        return geometry.bearing(a, b) if a != b else 0.0

    @staticmethod
    def _nearest_segment(point, polyline) -> tuple:
        """Distance (m) to the closest segment of polyline, and that segment's bearing."""
        best_dist, best_bearing = float("inf"), 0.0
        for j in range(len(polyline) - 1):
            dist = geometry.point_to_segment_distance(point, polyline[j], polyline[j + 1])
            if dist < best_dist:
                best_dist = dist
                best_bearing = geometry.bearing(polyline[j], polyline[j + 1])
        return best_dist, best_bearing

    def _connected(self, endpoints, link_a, link_b) -> bool:
        a = endpoints.get(link_a)
        b = endpoints.get(link_b)
        if a is None or b is None:
            return False
        return min(
            geometry.haversine(a[0], b[0]), geometry.haversine(a[0], b[1]),
            geometry.haversine(a[1], b[0]), geometry.haversine(a[1], b[1]),
        ) < self.connect_gap_m

    def _prune_offtrack(self, matched: list, geoms: dict, coords: list) -> list:
        """Assemble the matched links and compare against the original track: drop an interior
        segment whose geometry strays more than track_fit_m from the GPS track.

        This is the global "does the joined path follow the real route" check — it removes
        abrupt off-route segments (e.g. a link bulging off to one side) that local seam checks
        miss. End segments are left alone (a full link legitimately extends past the track ends).
        """
        if len(matched) < 3 or len(coords) < 2:
            return matched

        def deviation(ml):
            poly = geoms.get(ml.link_id, [])
            if not poly:
                return 0.0
            return max(
                min(geometry.point_to_segment_distance(p, coords[j], coords[j + 1])
                    for j in range(len(coords) - 1))
                for p in poly
            )

        items = list(matched)
        changed = True
        while changed and len(items) >= 3:
            changed = False
            for i in range(1, len(items) - 1):
                if deviation(items[i]) > self.track_fit_m:
                    del items[i]
                    changed = True
                    break
        return items

    def _load_roadclass(self, link_ids) -> dict:
        ids = list(link_ids)
        if not ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(road_link.c.link_id, road_link.c.roadclass)
                .where(road_link.c.link_id.in_(ids))
            ).all()
        return {row.link_id: row.roadclass for row in rows}

    def _load_candidate_geometries(self, coords) -> dict:
        lngs = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        margin = self.tolerance_m * _DEG_PER_M * 2.0
        min_lng, max_lng = min(lngs) - margin, max(lngs) + margin
        min_lat, max_lat = min(lats) - margin, max(lats) + margin

        with self.engine.connect() as conn:
            candidate_ids = set(
                conn.execute(
                    select(road_link_coord.c.link_id)
                    .where(road_link_coord.c.longitude.between(min_lng, max_lng))
                    .where(road_link_coord.c.latitude.between(min_lat, max_lat))
                    .distinct()
                ).scalars().all()
            )
            if not candidate_ids:
                return {}
            rows = conn.execute(
                select(
                    road_link_coord.c.link_id,
                    road_link_coord.c.longitude,
                    road_link_coord.c.latitude,
                )
                .where(road_link_coord.c.link_id.in_(candidate_ids))
                .order_by(road_link_coord.c.link_id, road_link_coord.c.seq)
            ).all()

        geoms: dict[int, list] = {}
        for row in rows:
            geoms.setdefault(row.link_id, []).append((row.longitude, row.latitude))
        return geoms
