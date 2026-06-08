"""需求3 扩展 SDK：相邻两站之间各路段的路况与长度占比，供前端渲染站间线段配色。

纯算法函数（无 DB，可单测）+ StationTrafficResolver（读 transit_segment + traffic_status）。
坐标 (经度, 纬度)，与 geometry 一致。
"""
import math
from dataclasses import dataclass

from sqlalchemy import Engine, select

from amap_service.db.schema import traffic_status, transit_segment
from amap_service.sdk import geometry

_EPS_M = 1e-6


@dataclass
class ChainLink:
    link_id: int
    points: list[tuple[float, float]]  # [(lng, lat), ...] 行进方向
    arc_start: float       # 链上累计弧长(米)起点
    arc_end: float         # 终点


def build_chain(segments: list[dict]) -> list[ChainLink]:
    """把有序 transit_segment 段拼成带累计弧长的链。每段用其 line_track 几何，
    弧长跨段连续累加；不足两点的段跳过。同一 link_id 出现两遍 => 两个独立区间。"""
    chain: list[ChainLink] = []
    arc = 0.0
    for seg in segments:
        pts = geometry.parse_track(seg["line_track"]) if seg.get("line_track") else []
        if len(pts) < 2:
            continue
        seg_len = sum(geometry.haversine(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        chain.append(ChainLink(seg["link_id"], pts, arc, arc + seg_len))
        arc += seg_len
    return chain


def _link_at(chain: list[ChainLink], arc: float) -> int | None:
    """覆盖给定弧长的 link_id（超出链范围则钳到最近端点对应的 link）。"""
    for cl in chain:
        if cl.arc_start - _EPS_M <= arc <= cl.arc_end + _EPS_M:
            return cl.link_id
    if not chain:
        return None
    return chain[0].link_id if arc < chain[0].arc_start else chain[-1].link_id


def section_links(chain: list[ChainLink], s_lo: float, s_hi: float) -> list[tuple[int, float]]:
    """弧长区间 [s_lo, s_hi] 内每条 link 的重叠长度，按链顺序，重叠>0 才列。
    零长区间（两站投到同一点）兜底为「覆盖点所在 link」占满（长度 1.0 -> 占比 100）。"""
    if s_hi - s_lo <= _EPS_M:
        lid = _link_at(chain, s_hi)
        return [(lid, 1.0)] if lid is not None else []
    out = []
    for cl in chain:
        ov = min(s_hi, cl.arc_end) - max(s_lo, cl.arc_start)
        if ov > _EPS_M:
            out.append((cl.link_id, ov))
    return out


def largest_remainder(lengths: list[float]) -> list[int]:
    """把各长度按比例取整到百分比，和恒为 100（最大余额法）。空表返回空；总长<=0 全 0。"""
    n = len(lengths)
    if n == 0:
        return []
    total = sum(lengths)
    if total <= 0:
        return [0] * n
    raw = [x / total * 100 for x in lengths]
    floors = [int(math.floor(r)) for r in raw]
    rem = 100 - sum(floors)
    order = sorted(range(n), key=lambda i: (raw[i] - floors[i], i), reverse=True)
    for k in range(rem):
        floors[order[k]] += 1
    return floors


def sample_chain(chain: list[ChainLink], step_m: float = 4.0) -> list[tuple[float, tuple[float, float]]]:
    """沿链按 step_m 采样，返回 [(arc, (lng,lat)), ...]，arc 非递减、含每段端点。"""
    samples: list[tuple[float, tuple[float, float]]] = []
    for cl in chain:
        pts = cl.points
        local = 0.0
        samples.append((cl.arc_start, pts[0]))
        for a, b in zip(pts, pts[1:]):
            d = geometry.haversine(a, b)
            if d <= 0:
                continue
            n = int(d // step_m)
            for k in range(1, n + 1):
                t = k * step_m / d
                samples.append((cl.arc_start + local + k * step_m,
                                (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)))
            local += d
            samples.append((cl.arc_start + local, b))
    return samples


def align_stations(samples: list[tuple[float, tuple[float, float]]], stations: list[tuple[float, float]]) -> list[float]:
    """把站点(按 LevelId 顺序)单调对齐到链上，返回每站的弧长位置(非递减)。

    DP：dp[i][j] = 站 i 落到采样点 j、且 j 的 arc >= 站 i-1 所选 arc 时，各站到落点距离和的最小值。
    用前缀最小值(带 argmin)在 O(站数×采样数) 内求解，回溯得各站弧长。"""
    M, N = len(samples), len(stations)
    if N == 0:
        return []
    if M == 0:
        return [0.0] * N
    arcs = [s[0] for s in samples]
    pts = [s[1] for s in samples]

    def cost(i, j):
        return geometry.haversine(stations[i], pts[j])

    dp = [cost(0, j) for j in range(M)]
    back = [[-1] * M for _ in range(N)]
    for i in range(1, N):
        best, best_k = float("inf"), 0
        new = [0.0] * M
        for j in range(M):
            if dp[j] < best:
                best, best_k = dp[j], j
            new[j] = cost(i, j) + best
            back[i][j] = best_k
        dp = new
    end = min(range(M), key=lambda j: dp[j])
    chosen = [0] * N
    chosen[N - 1] = end
    for i in range(N - 1, 0, -1):
        chosen[i - 1] = back[i][chosen[i]]
    return [arcs[chosen[i]] for i in range(N)]


def _data_of(line_object: dict) -> dict:
    """容忍带 Data 包裹或已解包两种形态。"""
    if not isinstance(line_object, dict):
        return {}
    data = line_object.get("Data")
    return data if isinstance(data, dict) else line_object


def _line_name_of(line_object: dict) -> str:
    return str(_data_of(line_object).get("LineName") or "")


def _stations_for(line_object: dict, direction: int) -> list[tuple[int, float, float]]:
    """方向(0->UpObject,1->DownObject)的站点 [(LevelId, lng, lat), ...]，按 LevelId 升序；
    缺方向对象或缺坐标的站点丢弃。"""
    data = _data_of(line_object)
    obj = data.get("UpObject" if direction == 0 else "DownObject")
    if not isinstance(obj, dict):
        return []
    out = []
    for s in obj.get("Stations") or []:
        if not isinstance(s, dict):
            continue
        lvl, lng, lat = s.get("LevelId"), s.get("Lon02"), s.get("Lat02")
        if lvl is None or lng is None or lat is None:
            continue
        out.append((lvl, lng, lat))
    out.sort(key=lambda x: x[0])
    return out


class StationTrafficResolver:
    """读 transit_segment + traffic_status，给出相邻两站之间各路段的路况与长度占比。"""

    def __init__(self, engine: Engine, sample_step_m: float = 4.0, default_state: int = 1):
        self.engine = engine
        self.sample_step_m = sample_step_m
        self.default_state = default_state

    def station_section(self, line_object: dict, direction: int, level_id: int) -> list[dict]:
        """方法一：站 level_id-1 -> 站 level_id 之间的路段列表。
        -> [{"link_id", "state", "pct"}, ...]，pct 之和=100；无数据/越界返回 []。"""
        stations = _stations_for(line_object, direction)
        idx = next((k for k, s in enumerate(stations) if s[0] == level_id), None)
        if idx is None or idx == 0:
            return []
        chain = build_chain(self._load_segments(_line_name_of(line_object), direction))
        if not chain:
            return []
        arcs = align_stations(sample_chain(chain, self.sample_step_m),
                              [(lng, lat) for _, lng, lat in stations])
        pairs = section_links(chain, arcs[idx - 1], arcs[idx])
        traffic = self._load_traffic([lid for lid, _ in pairs])
        return self._entries(pairs, traffic)

    def line_sections(self, line_object: dict) -> dict:
        """方法二：整条线路所有方向、所有站间区间。
        -> { direction: [ {level_id: [ {link_id, state, pct}, ... ]}, ... ] }
        缺方向/无 transit_segment 的方向不出现。"""
        line_name = _line_name_of(line_object)
        result: dict = {}
        for direction in (0, 1):
            stations = _stations_for(line_object, direction)
            if len(stations) < 2:
                continue
            chain = build_chain(self._load_segments(line_name, direction))
            if not chain:
                continue
            traffic = self._load_traffic([cl.link_id for cl in chain])
            arcs = align_stations(sample_chain(chain, self.sample_step_m),
                                  [(lng, lat) for _, lng, lat in stations])
            dir_list = []
            for idx in range(1, len(stations)):
                level_id = stations[idx][0]
                pairs = section_links(chain, arcs[idx - 1], arcs[idx])
                dir_list.append({level_id: self._entries(pairs, traffic)})
            result[direction] = dir_list
        return result

    def _entries(self, pairs: list[tuple[int, float]], traffic: dict) -> list[dict]:
        pcts = largest_remainder([ov for _, ov in pairs])
        return [{"link_id": lid, "state": traffic.get(lid, self.default_state), "pct": p}
                for (lid, _), p in zip(pairs, pcts)]

    def _load_segments(self, line_name: str, direction: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(transit_segment.c.link_id, transit_segment.c.line_track)
                .where((transit_segment.c.line_name == line_name)
                       & (transit_segment.c.direction == direction))
                .order_by(transit_segment.c.seq)
            ).all()
        return [{"link_id": r.link_id, "line_track": r.line_track} for r in rows]

    def _load_traffic(self, link_ids: list[int]) -> dict:
        if not link_ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(traffic_status.c.link_id, traffic_status.c.state)
                .where(traffic_status.c.link_id.in_(set(link_ids)))
            ).all()
        return {r.link_id: r.state for r in rows}
