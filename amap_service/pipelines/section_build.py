"""需求2/3：把每条线路每个方向的「站间路段占比」算出来落 transit_section_link（静态，不含路况）。

纯 DB→DB：读 transit_segment（链）+ transit_station（站坐标），跑 section_compute 几何算法，
按 (line_name, direction) 整体替换写入。依赖 transit-build 已填好 segment 与 station。
"""
import logging

from sqlalchemy import Engine, select

from amap_service.db.repositories import replace_transit_section_links
from amap_service.db.schema import transit_segment, transit_station
from amap_service.sdk.section_compute import build_chain, compute_section_rows

logger = logging.getLogger(__name__)


def run_section_build(engine: Engine, config) -> dict:
    step = config.sdk.section_sample_step_m
    stats = {"lines": 0, "directions": 0, "sections": 0, "skipped_directions": 0}

    with engine.connect() as conn:
        pairs = conn.execute(
            select(transit_segment.c.line_name, transit_segment.c.direction,
                   transit_segment.c.nor_code).distinct()
        ).all()
    seen_lines = set()
    for line_name, direction, nor_code in pairs:
        try:
            segments = _load_segments(engine, line_name, direction)
            stations = _load_stations(engine, line_name, direction)
            chain = build_chain(segments)
            rows = compute_section_rows(chain, stations, step)
            written = replace_transit_section_links(engine, line_name, direction, nor_code, rows)
            if written:
                stats["directions"] += 1
                stats["sections"] += written
                seen_lines.add(line_name)
            logger.info("section build: %s dir %s -> %d rows", line_name, direction, written)
        except Exception:  # noqa: BLE001 - one bad line/direction must not abort the whole build
            logger.exception("section build: line '%s' dir %s failed; skipping", line_name, direction)
            stats["skipped_directions"] += 1
    stats["lines"] = len(seen_lines)
    logger.info("section build: done %s", stats)
    return stats


def _load_segments(engine: Engine, line_name: str, direction) -> list:
    with engine.connect() as conn:
        rows = conn.execute(
            select(transit_segment.c.link_id, transit_segment.c.line_track)
            .where((transit_segment.c.line_name == line_name)
                   & (transit_segment.c.direction == direction))
            .order_by(transit_segment.c.seq)
        ).all()
    return [{"link_id": r.link_id, "line_track": r.line_track} for r in rows]


def _load_stations(engine: Engine, line_name: str, direction) -> list:
    with engine.connect() as conn:
        rows = conn.execute(
            select(transit_station.c.level_id, transit_station.c.longitude,
                   transit_station.c.latitude)
            .where((transit_station.c.line_name == line_name)
                   & (transit_station.c.direction == direction))
            .order_by(transit_station.c.level_id)
        ).all()
    return [(r.level_id, r.longitude, r.latitude) for r in rows]
