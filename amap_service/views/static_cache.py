"""已建线路静态结构(segment/section/station)的进程内缓存。

一次性批量加载所有已建线路的有序路段与站间占比进内存,供 API 与 MQTT 发布器共用。
靠 transit_segment.created_at / transit_section_link.built_at 的最大值作版本探针:
线路结构只在 transit-build/section-build 后才变(每天级),版本不变则一直命中内存。
ttl_seconds 控制"多久才去校验一次版本"(0=每次校验);版本不变时即便校验也不重载。
"""
import time

from sqlalchemy import Engine, func, select

from amap_service.db.schema import (
    road_link, transit_segment, transit_section_link, transit_station,
)

# 单条 IN(...) 的最大绑定参数数:保守取 2000,远低于 SQLite 32766 上限,MySQL 亦安全。
_IN_CHUNK = 2000


class StaticLineCache:
    def __init__(self, engine: Engine, ttl_seconds: int = 0):
        self.engine = engine
        self.ttl_seconds = ttl_seconds
        self._segments = {}      # line -> {direction: [ {seq,link_id,reverse,line_track} ]}
        self._sections = {}      # line -> {direction: [ {from_level_id,to_level_id,links:[...]} ]}
        self._link_tracks = {}   # link_id -> road_link.line_track
        self._lines = []         # [ {line_name,directions,has_segments,has_sections,station_count} ]
        self._version = None
        self._checked_at = None

    def segments(self, line_name: str) -> dict:
        self._ensure()
        return self._segments.get(line_name, {})

    def sections(self, line_name: str) -> dict:
        self._ensure()
        return self._sections.get(line_name, {})

    def link_track(self, link_id: int):
        self._ensure()
        return self._link_tracks.get(link_id)

    def lines(self) -> list:
        self._ensure()
        return self._lines

    # ── internals ──────────────────────────────────────────
    def _ensure(self) -> None:
        now = time.monotonic()
        if self._version is not None and self.ttl_seconds > 0 \
                and self._checked_at is not None and (now - self._checked_at) < self.ttl_seconds:
            return
        version = self._probe_version()
        self._checked_at = now
        if version == self._version and self._version is not None:
            return
        self._reload()
        self._version = version

    def _probe_version(self):
        # 版本 = (行数, 最大时间戳)。带行数才能检出"同一秒内"或不推进 max 的增删
        # (created_at/built_at 仅秒级精度;只比 max 时间戳会漏掉同秒重建与删除)。
        with self.engine.connect() as conn:
            seg_n = conn.execute(select(func.count()).select_from(transit_segment)).scalar()
            seg_v = conn.execute(select(func.max(transit_segment.c.created_at))).scalar()
            sec_n = conn.execute(select(func.count()).select_from(transit_section_link)).scalar()
            sec_v = conn.execute(select(func.max(transit_section_link.c.built_at))).scalar()
        return (seg_n, str(seg_v), sec_n, str(sec_v))

    def _reload(self) -> None:
        with self.engine.connect() as conn:
            seg_rows = conn.execute(
                select(transit_segment.c.line_name, transit_segment.c.direction,
                       transit_segment.c.seq, transit_segment.c.link_id,
                       transit_segment.c.reverse_coords, transit_segment.c.line_track)
                .order_by(transit_segment.c.line_name, transit_segment.c.direction,
                          transit_segment.c.seq)
            ).all()
            sec_rows = conn.execute(
                select(transit_section_link.c.line_name, transit_section_link.c.direction,
                       transit_section_link.c.from_level_id, transit_section_link.c.to_level_id,
                       transit_section_link.c.seq, transit_section_link.c.link_id,
                       transit_section_link.c.length_m, transit_section_link.c.pct)
                .order_by(transit_section_link.c.line_name, transit_section_link.c.direction,
                          transit_section_link.c.to_level_id, transit_section_link.c.seq)
            ).all()
            station_rows = conn.execute(
                select(transit_station.c.line_name, transit_station.c.direction,
                       func.count().label("n"))
                .group_by(transit_station.c.line_name, transit_station.c.direction)
            ).all()

        segments, sections = {}, {}
        for r in seg_rows:
            segments.setdefault(r.line_name, {}).setdefault(r.direction, []).append(
                {"seq": r.seq, "link_id": r.link_id,
                 "reverse": r.reverse_coords, "line_track": r.line_track})
        for r in sec_rows:
            dir_secs = sections.setdefault(r.line_name, {}).setdefault(r.direction, [])
            if not dir_secs or dir_secs[-1]["to_level_id"] != r.to_level_id:
                dir_secs.append({"from_level_id": r.from_level_id,
                                 "to_level_id": r.to_level_id, "links": []})
            dir_secs[-1]["links"].append(
                {"link_id": r.link_id, "length_m": r.length_m, "pct": r.pct})

        link_ids = list({lk["link_id"] for line in sections.values()
                         for dirsecs in line.values() for sec in dirsecs for lk in sec["links"]})
        link_tracks = {}
        if link_ids:
            # 分批 IN 查询: link_ids 可达数万,单条 IN(...) 会超出 SQLite 的
            # SQLITE_MAX_VARIABLE_NUMBER(默认 32766)并触发 OperationalError。
            with self.engine.connect() as conn:
                for i in range(0, len(link_ids), _IN_CHUNK):
                    chunk = link_ids[i:i + _IN_CHUNK]
                    for r in conn.execute(
                        select(road_link.c.link_id, road_link.c.line_track)
                        .where(road_link.c.link_id.in_(chunk))
                    ).all():
                        link_tracks[r.link_id] = r.line_track

        stations = {(r.line_name, r.direction): r.n for r in station_rows}
        names = set(segments) | set(sections)
        lines = []
        for name in sorted(names):
            dirs = sorted(set(segments.get(name, {})) | set(sections.get(name, {})))
            lines.append({
                "line_name": name,
                "directions": dirs,
                "has_segments": name in segments,
                "has_sections": name in sections,
                "station_count": sum(stations.get((name, d), 0) for d in dirs),
            })

        self._segments, self._sections = segments, sections
        self._link_tracks, self._lines = link_tracks, lines
