"""查询层 SDK：给定线路名称，返回相邻两站之间各路段的路况(state)与长度占比(pct)。

读静态表 transit_section_link（由 section-build 预计算）+ 实时表 traffic_status，
不跑任何几何。路况缺失默认 default_state(1)。
"""
from sqlalchemy import Engine, select

from amap_service.db.schema import traffic_status, transit_section_link


class StationTrafficResolver:
    def __init__(self, engine: Engine, default_state: int = 1):
        self.engine = engine
        self.default_state = default_state

    def station_section(self, line_name: str, direction: int, level_id: int) -> list[dict]:
        """方法一：站 (level_id 的上一站) -> 站 level_id 之间的路段列表。
        -> [{"link_id", "state", "pct"}, ...]，pct 之和=100；无数据返回 []。"""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(transit_section_link.c.link_id, transit_section_link.c.pct)
                .where((transit_section_link.c.line_name == line_name)
                       & (transit_section_link.c.direction == direction)
                       & (transit_section_link.c.to_level_id == level_id))
                .order_by(transit_section_link.c.seq)
            ).all()
        if not rows:
            return []
        traffic = self._load_traffic([r.link_id for r in rows])
        return [{"link_id": r.link_id, "state": traffic.get(r.link_id, self.default_state),
                 "pct": r.pct} for r in rows]

    def line_sections(self, line_name: str) -> dict:
        """方法二：整条线路所有方向、所有站间区间。
        -> { direction: [ {to_level_id: [ {link_id, state, pct}, ... ]}, ... ] }；无数据返回 {}。"""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(transit_section_link.c.direction, transit_section_link.c.to_level_id,
                       transit_section_link.c.seq, transit_section_link.c.link_id,
                       transit_section_link.c.pct)
                .where(transit_section_link.c.line_name == line_name)
                .order_by(transit_section_link.c.direction, transit_section_link.c.to_level_id,
                          transit_section_link.c.seq)
            ).all()
        if not rows:
            return {}
        traffic = self._load_traffic([r.link_id for r in rows])
        result: dict = {}
        for r in rows:
            dir_list = result.setdefault(r.direction, [])
            if not dir_list or list(dir_list[-1].keys())[0] != r.to_level_id:
                dir_list.append({r.to_level_id: []})
            dir_list[-1][r.to_level_id].append(
                {"link_id": r.link_id, "state": traffic.get(r.link_id, self.default_state),
                 "pct": r.pct})
        return result

    def _load_traffic(self, link_ids: list) -> dict:
        if not link_ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(traffic_status.c.link_id, traffic_status.c.state)
                .where(traffic_status.c.link_id.in_(set(link_ids)))
            ).all()
        return {r.link_id: r.state for r in rows}
