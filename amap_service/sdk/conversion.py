"""Public SDK: bidirectional GPS-track ↔ ordered road-link conversion."""
from dataclasses import dataclass

from sqlalchemy import Engine, select

from amap_service.db.schema import road_link_coord
from amap_service.sdk import geometry
from amap_service.sdk.matcher import LinkMatcher


@dataclass
class LinkInfo:
    link_id: int
    reverse_coords: bool


class TrackConverter:
    def __init__(self, engine: Engine, tolerance_m: float = 30.0, reverse_angle_deg: float = 90.0,
                 against_track_deg: float = 120.0, loop_window: int = 8, loop_return_m: float = 10.0,
                 jut_deg: float = 60.0, jut_neighbor_deg: float = 45.0, jut_offtrack_m: float = 15.0):
        self.engine = engine
        self.reverse_angle_deg = reverse_angle_deg
        self.against_track_deg = against_track_deg   # drop a segment heading more than this against the route
        self.loop_window = loop_window               # look this many segments ahead for a return-to-start loop
        self.loop_return_m = loop_return_m           # a segment start this close to an earlier end = loop
        self.jut_deg = jut_deg                       # a segment turning more than this off BOTH neighbours = jut
        self.jut_neighbor_deg = jut_neighbor_deg     # ...while the neighbours agree within this (route goes straight)
        self.jut_offtrack_m = jut_offtrack_m         # ...and only if it also strays this far off the track
        self.matcher = LinkMatcher(engine, tolerance_m, reverse_angle_deg=reverse_angle_deg)

    def _matched_segments(self, track: str) -> list[dict]:
        """Match a GPS track to an ordered sequence of road links and emit each link's FULL
        stored geometry, oriented in travel direction.

        Real road networks are fine-grained, so a track maps to whole road-network links; we
        emit the complete link geometry (no clipping). The direction-aware matcher already
        chooses the correct one-way link on divided roads, so `reverse` is set only for genuine
        two-way reversals. Returns [{link_id, reverse_coords, coords(list), line_track(str)}, ...].
        """
        coords = geometry.parse_track(track)
        matched = self.matcher.match_track(coords)
        if not matched:
            return []
        geoms = self._load_geometries([m.link_id for m in matched])
        segments: list[dict] = []
        for m in matched:
            polyline = list(geoms.get(m.link_id, []))
            if m.reverse:
                polyline = polyline[::-1]
            segments.append({
                "link_id": m.link_id,
                "reverse_coords": m.reverse,
                "coords": polyline,
                "line_track": geometry.format_track(polyline),
            })
        return segments

    def linetrack_to_linkinfos(self, track: str) -> list[LinkInfo]:
        """GPS track string → ordered LinkInfo list with reverse_coords flags."""
        return [
            LinkInfo(link_id=s["link_id"], reverse_coords=s["reverse_coords"])
            for s in self._matched_segments(track)
        ]

    def linetrack_to_segments(self, track: str, passes: int = 1,
                              densify_step_m: float = 15.0) -> list[dict]:
        """GPS track → ordered segments {link_id, reverse_coords, line_track} (full link geometry,
        oriented in travel direction). Used to build transit_segment rows.

        passes > 1 runs a second matching pass: the first-pass segments are joined end-to-end and
        re-sampled (densify_step_m) into a clean, drift-free track that is matched again — this
        fills small gaps left by GPS drift, yielding a more continuous segment chain.
        """
        segments = [
            {"link_id": s["link_id"], "reverse_coords": s["reverse_coords"], "line_track": s["line_track"]}
            for s in self._matched_segments(track)
        ]
        for _ in range(max(0, passes - 1)):
            if not segments:
                break
            assembled = self.assemble_track(segments)
            if not assembled:
                break
            refined_track = geometry.format_track(
                geometry.densify(geometry.parse_track(assembled), densify_step_m)
            )
            segments = [
                {"link_id": s["link_id"], "reverse_coords": s["reverse_coords"],
                 "line_track": s["line_track"]}
                for s in self._matched_segments(refined_track)
            ]
        # final coherence cleanup vs the ORIGINAL track: drop opposite-direction picks and
        # return-to-start loops (e.g. excursions onto the opposite carriageway and back).
        coords = geometry.parse_track(track)
        segments = self._drop_against_track(segments, coords)
        segments = self._drop_loops(segments)
        segments = self._drop_juts(segments, coords)
        return segments

    @staticmethod
    def _seg_ends(seg):
        pts = geometry.parse_track(seg["line_track"])
        return (pts[0], pts[-1]) if len(pts) >= 2 else (None, None)

    @staticmethod
    def _track_bearing_at(coords, point):
        best_dist, best_bearing = float("inf"), 0.0
        for i in range(len(coords) - 1):
            d = geometry.point_to_segment_distance(point, coords[i], coords[i + 1])
            if d < best_dist:
                best_dist, best_bearing = d, geometry.bearing(coords[i], coords[i + 1])
        return best_bearing

    @staticmethod
    def _doubled_track_mask(coords: list, index_gap: int = 5, dist_m: float = 20.0,
                            angle_deg: float = 120.0) -> list:
        """Per-vertex flag: True where the GPS track itself doubles back (passes the same place
        again far later in opposite direction). Reversed segments here are legitimate two-way
        traversals (undivided road / real turnaround), not opposite-carriageway mismatches."""
        n = len(coords)

        def local_bearing(i):
            a = coords[max(0, i - 1)]
            b = coords[min(n - 1, i + 1)]
            return geometry.bearing(a, b)

        mask = [False] * n
        bearings = [local_bearing(i) for i in range(n)]
        for i in range(n):
            if mask[i]:
                continue
            for j in range(i + index_gap, n):
                if (geometry.haversine(coords[i], coords[j]) < dist_m
                        and geometry.angle_diff(bearings[i], bearings[j]) > angle_deg):
                    mask[i] = mask[j] = True
                    break
        return mask

    def _drop_against_track(self, segments: list, coords: list) -> list:
        """Drop segments whose travel direction opposes the original track's local direction
        (opposite-carriageway / backtrack picks) — but ONLY where the track does NOT itself
        double back. Where the track genuinely traverses a road both ways, the reversed segment
        is a legitimate return leg and is kept."""
        if len(coords) < 2:
            return segments
        doubled = self._doubled_track_mask(coords)
        kept = []
        for seg in segments:
            pts = geometry.parse_track(seg["line_track"])
            if len(pts) >= 2:
                seg_bearing = geometry.bearing(pts[0], pts[-1])
                mid = pts[len(pts) // 2]
                ni = min(range(len(coords)), key=lambda k: geometry.haversine(coords[k], mid))
                if (not doubled[ni]
                        and geometry.angle_diff(seg_bearing, self._track_bearing_at(coords, mid)) > self.against_track_deg):
                    continue
            kept.append(seg)
        return kept

    def _drop_juts(self, segments: list, coords: list) -> list:
        """Remove a 'jut': a segment that turns sharply away from BOTH neighbours while those
        neighbours keep the same heading (route goes straight) AND that strays off the original
        track — i.e. a GPS-drift pick onto a perpendicular cross street the bus did not drive.

        A genuine turn has differently-headed neighbours (protected by jut_neighbor_deg); a real
        on-route detour stays ON the track (protected by jut_offtrack_m), so both are preserved.
        """
        def bearing(seg):
            pts = geometry.parse_track(seg["line_track"])
            return geometry.bearing(pts[0], pts[-1]) if len(pts) >= 2 else None

        def deviation(seg):
            pts = geometry.parse_track(seg["line_track"])
            if not pts or len(coords) < 2:
                return 0.0
            return max(min(geometry.point_to_segment_distance(p, coords[j], coords[j + 1])
                           for j in range(len(coords) - 1)) for p in pts)

        items = list(segments)
        changed = True
        while changed and len(items) >= 3:
            changed = False
            for i in range(1, len(items) - 1):
                bp, bc, bn = bearing(items[i - 1]), bearing(items[i]), bearing(items[i + 1])
                if None in (bp, bc, bn):
                    continue
                if (geometry.angle_diff(bc, bp) > self.jut_deg
                        and geometry.angle_diff(bc, bn) > self.jut_deg
                        and geometry.angle_diff(bp, bn) < self.jut_neighbor_deg
                        and deviation(items[i]) > self.jut_offtrack_m):
                    del items[i]
                    changed = True
                    break
        return items

    def _drop_loops(self, segments: list) -> list:
        """Remove return-to-start excursions: if a later segment starts where an earlier one
        ended, the segments in between looped out and back — drop them."""
        items = list(segments)
        changed = True
        while changed and len(items) >= 3:
            changed = False
            for i in range(len(items) - 2):
                end_i = self._seg_ends(items[i])[1]
                if end_i is None:
                    continue
                for j in range(i + 2, min(len(items), i + 1 + self.loop_window)):
                    start_j = self._seg_ends(items[j])[0]
                    if start_j and geometry.haversine(end_i, start_j) < self.loop_return_m:
                        del items[i + 1:j]
                        changed = True
                        break
                if changed:
                    break
        return items

    def assemble_track(self, segments: list) -> str:
        """Join segment geometries end-to-end into one continuous track string (seam dups removed)."""
        points: list = []
        for seg in segments:
            for p in geometry.parse_track(seg["line_track"]):
                if points and geometry.haversine(points[-1], p) < 1e-6:
                    continue
                points.append(p)
        return geometry.format_track(points)

    def linkinfos_to_tracks(self, linkinfos: list[LinkInfo]) -> str:
        """Ordered LinkInfo list → concatenated GPS track string (seam endpoints de-duped)."""
        if not linkinfos:
            return ""
        geoms = self._load_geometries([li.link_id for li in linkinfos])
        merged: list[tuple[float, float]] = []
        for li in linkinfos:
            polyline = list(geoms.get(li.link_id, []))
            if li.reverse_coords:
                polyline = polyline[::-1]
            for point in polyline:
                if merged and merged[-1] == point:
                    continue  # drop duplicated seam endpoint
                merged.append(point)
        return geometry.format_track(merged)

    def link_tracks(self, linkinfos: list[LinkInfo]) -> list[str]:
        """Per-link track strings (each link's stored coords, reversed when reverse_coords).

        Unlike linkinfos_to_tracks (which concatenates+de-dups into one string), this returns
        one track per link, in order — for storing each segment's own geometry.
        One geometry load for the whole list.
        """
        geoms = self._load_geometries([li.link_id for li in linkinfos])
        tracks = []
        for li in linkinfos:
            polyline = list(geoms.get(li.link_id, []))
            if li.reverse_coords:
                polyline = polyline[::-1]
            tracks.append(geometry.format_track(polyline))
        return tracks

    def _load_geometries(self, link_ids: list[int]) -> dict:
        if not link_ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    road_link_coord.c.link_id,
                    road_link_coord.c.longitude,
                    road_link_coord.c.latitude,
                )
                .where(road_link_coord.c.link_id.in_(set(link_ids)))
                .order_by(road_link_coord.c.link_id, road_link_coord.c.seq)
            ).all()
        geoms: dict[int, list] = {}
        for row in rows:
            geoms.setdefault(row.link_id, []).append((row.longitude, row.latitude))
        return geoms
