"""Graph-based connectivity repair: insert the links missing between consecutive
matched segments so the segment chain is topologically continuous.

The greedy matcher emits whole road-network links and the cleanup filters drop some,
so two consecutive segments may not share an endpoint — a short connecting link was
either never picked (out-competed at every GPS point by a parallel/overlapping link)
or removed by a cleanup filter. Both leave a hole. Here, for every pair of segments
whose open ends are farther apart than `connect_gap_m`, we route the shortest path
through the road-link graph (links sharing endpoints) between those ends and splice
the intermediate links back in, oriented in travel direction.

Reads `road_link_coord` geometry only, scoped to a small bbox around each gap.
"""
import heapq

from sqlalchemy import Engine, select

from amap_service.db.schema import road_link_coord
from amap_service.sdk import geometry

# Rough degrees-per-meter for bbox expansion (latitude; longitude is tighter but
# over-expanding the search box is harmless — only the routing result matters).
_DEG_PER_M = 1.0 / 111320.0


class GraphConnector:
    def __init__(self, engine: Engine, connect_gap_m: float = 8.0, node_snap_m: float = 10.0,
                 max_fill_links: int = 8, detour_factor: float = 4.0,
                 detour_slack_m: float = 150.0, search_margin_m: float = 120.0):
        self.engine = engine
        self.connect_gap_m = connect_gap_m       # ends closer than this are already connected
        self.node_snap_m = node_snap_m           # endpoints within this distance are the same graph node
        self.max_fill_links = max_fill_links     # never splice in more than this many links per gap
        self.detour_factor = detour_factor       # reject a route longer than gap * this ...
        self.detour_slack_m = detour_slack_m     # ... or gap + this (whichever is larger)
        self.search_margin_m = search_margin_m   # candidate-link bbox margin around the gap

    def fill(self, segments: list[dict]) -> list[dict]:
        """Return segments with missing connecting links spliced in between any
        consecutive pair whose end→start gap exceeds connect_gap_m."""
        if len(segments) < 2:
            return segments
        out = [segments[0]]
        for nxt in segments[1:]:
            end = self._end(out[-1])
            start = self._start(nxt)
            if end and start and geometry.haversine(end, start) > self.connect_gap_m:
                out.extend(self._route(end, start, exclude={out[-1]["link_id"], nxt["link_id"]}))
            out.append(nxt)
        return out

    @staticmethod
    def _pts(seg: dict) -> list:
        return geometry.parse_track(seg["line_track"])

    def _end(self, seg: dict):
        pts = self._pts(seg)
        return pts[-1] if pts else None

    def _start(self, seg: dict):
        pts = self._pts(seg)
        return pts[0] if pts else None

    @staticmethod
    def _length(poly: list) -> float:
        return sum(geometry.haversine(poly[i], poly[i + 1]) for i in range(len(poly) - 1))

    def _route(self, src, dst, exclude: set) -> list[dict]:
        """Shortest link-path from src to dst through the road graph, oriented in travel
        direction. Returns [] if no acceptable route exists (so the gap is left as-is)."""
        links = {lid: poly for lid, poly in self._load_links(src, dst).items()
                 if lid not in exclude and len(poly) >= 2}
        if not links:
            return []

        nodes: list = []  # representative coord per graph node

        def node_of(pt):
            for idx, c in enumerate(nodes):
                if geometry.haversine(c, pt) <= self.node_snap_m:
                    return idx
            nodes.append(pt)
            return len(nodes) - 1

        adj: dict[int, list] = {}  # node -> [(other_node, link_id, forward, length, poly), ...]
        for lid, poly in links.items():
            u, v = node_of(poly[0]), node_of(poly[-1])
            if u == v:
                continue  # zero-length / self-loop link contributes no connectivity
            length = self._length(poly)
            adj.setdefault(u, []).append((v, lid, True, length, poly))
            adj.setdefault(v, []).append((u, lid, False, length, poly))

        s_node = self._nearest_node(nodes, src)
        d_node = self._nearest_node(nodes, dst)
        if s_node is None or d_node is None or s_node == d_node:
            return []

        edges = self._dijkstra(adj, s_node, d_node)
        if edges is None:
            return []

        total = sum(self._length(poly) for _, _, poly in edges)
        straight = geometry.haversine(src, dst)
        if len(edges) > self.max_fill_links:
            return []
        if total > max(straight * self.detour_factor, straight + self.detour_slack_m):
            return []  # routed path detours too far — likely not the real connector

        result = []
        for lid, forward, poly in edges:
            oriented = poly if forward else poly[::-1]
            result.append({
                "link_id": lid,
                "reverse_coords": not forward,
                "line_track": geometry.format_track(oriented),
            })
        return result

    def _nearest_node(self, nodes: list, pt):
        best, best_idx = self.node_snap_m, None
        for idx, c in enumerate(nodes):
            d = geometry.haversine(c, pt)
            if d <= best:
                best, best_idx = d, idx
        return best_idx

    @staticmethod
    def _dijkstra(adj: dict, src: int, dst: int):
        """Least-total-link-length path. Returns [(link_id, forward, poly), ...] in travel
        order, or None if dst is unreachable."""
        dist = {src: 0.0}
        prev: dict[int, tuple] = {}
        pq = [(0.0, src)]
        while pq:
            d_u, u = heapq.heappop(pq)
            if u == dst:
                break
            if d_u > dist.get(u, float("inf")):
                continue
            for v, lid, forward, length, poly in adj.get(u, []):
                nd = d_u + length
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = (u, lid, forward, poly)
                    heapq.heappush(pq, (nd, v))
        if dst != src and dst not in prev:
            return None
        edges = []
        cur = dst
        while cur != src:
            u, lid, forward, poly = prev[cur]
            edges.append((lid, forward, poly))
            cur = u
        edges.reverse()
        return edges

    def _load_links(self, src, dst) -> dict:
        margin = self.search_margin_m * _DEG_PER_M
        lo_lng, hi_lng = min(src[0], dst[0]) - margin, max(src[0], dst[0]) + margin
        lo_lat, hi_lat = min(src[1], dst[1]) - margin, max(src[1], dst[1]) + margin
        with self.engine.connect() as conn:
            ids = set(conn.execute(
                select(road_link_coord.c.link_id)
                .where(road_link_coord.c.longitude.between(lo_lng, hi_lng))
                .where(road_link_coord.c.latitude.between(lo_lat, hi_lat))
                .distinct()
            ).scalars().all())
            if not ids:
                return {}
            rows = conn.execute(
                select(
                    road_link_coord.c.link_id,
                    road_link_coord.c.longitude,
                    road_link_coord.c.latitude,
                )
                .where(road_link_coord.c.link_id.in_(ids))
                .order_by(road_link_coord.c.link_id, road_link_coord.c.seq)
            ).all()
        geoms: dict[int, list] = {}
        for row in rows:
            geoms.setdefault(row.link_id, []).append((row.longitude, row.latitude))
        return geoms
