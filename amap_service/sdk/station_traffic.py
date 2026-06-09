"""需求3 扩展 SDK：相邻两站之间各路段的路况与长度占比，供前端渲染站间线段配色。

纯算法函数（无 DB，可单测）+ StationTrafficResolver（读 transit_segment + traffic_status）。
坐标 (经度, 纬度)，与 geometry 一致。
"""
from sqlalchemy import Engine, select

from amap_service.db.schema import traffic_status, transit_segment
from amap_service.sdk.section_compute import (
    ChainLink, build_chain, _link_at, section_links, largest_remainder,
    sample_chain, align_stations,
)


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
