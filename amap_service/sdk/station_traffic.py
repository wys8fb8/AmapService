"""需求3 扩展 SDK：相邻两站之间各路段的路况与长度占比，供前端渲染站间线段配色。

纯算法函数（无 DB，可单测）+ StationTrafficResolver（读 transit_segment + traffic_status）。
坐标 (经度, 纬度)，与 geometry 一致。
"""
import math
from dataclasses import dataclass

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


def sample_chain(chain: list[ChainLink], step_m: float = 4.0) -> list[tuple]:
    """沿链按 step_m 采样，返回 [(arc, (lng,lat)), ...]，arc 非递减、含每段端点。"""
    samples: list[tuple] = []
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


def align_stations(samples: list[tuple], stations: list[tuple]) -> list[float]:
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
