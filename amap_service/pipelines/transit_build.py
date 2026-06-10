"""需求2 阶段二：把每条公交线路的方向轨迹经需求3 SDK 转成有序路段落库。

链路：token → 线路列表 → 逐条线路对象（GetRoadLineEntity）。对每个方向的
`LineLonLat` 轨迹调用 TrackConverter.linetrack_to_linkinfos，得到有序 LinkInfo，
按 (line_name, direction) 整段替换写入 transit_segment。

依赖：road_link_coord 必须已由需求1 路网落地填充（SDK 做 DB 空间匹配）。
单条线路失败跳过、继续其余（不中断整次运行）。
"""
import json
import logging

from sqlalchemy import Engine

from amap_service.clients.transit import TransitClient
from amap_service.db.repositories import (
    insert_transit_line_raw, replace_transit_segments, replace_transit_stations,
)
from amap_service.parsing.transit import extract_line_records, parse_line_stations, parse_line_tracks, select_line_names
from amap_service.pipelines.section_build import run_section_build
from amap_service.sdk import TrackConverter

logger = logging.getLogger(__name__)


def run_transit_build(engine: Engine, transit_client: TransitClient, config) -> dict:
    converter = TrackConverter(
        engine,
        tolerance_m=config.sdk.match_tolerance_m,
        reverse_angle_deg=config.sdk.reverse_angle_deg,
        against_track_deg=config.sdk.against_track_deg,
        loop_return_m=config.sdk.loop_return_m,
        jut_deg=config.sdk.jut_deg,
        jut_neighbor_deg=config.sdk.jut_neighbor_deg,
        jut_offtrack_m=config.sdk.jut_offtrack_m,
        against_window_frac=config.sdk.against_window_frac,
        against_window_m=config.sdk.against_window_m,
        connect_gap_m=config.sdk.connect_gap_m,
        max_fill_links=config.sdk.max_fill_links,
    )
    stats = {"token_ok": False, "lines": 0, "directions": 0, "segments": 0, "skipped_lines": 0, "sections": 0}

    token, _ = transit_client.get_token()
    if not token:
        logger.warning("transit build: token not extracted; set transit.token_path. Stopping.")
        return stats
    stats["token_ok"] = True

    raw_list = transit_client.get_line_list(token)
    t = config.transit
    try:
        records = extract_line_records(
            json.loads(raw_list), t.line_name_path, t.line_name_field, t.company_field
        )
    except Exception:  # noqa: BLE001
        records = []
    to_process = select_line_names(records, t.companys_set(), t.lines_set(), t.line_limit)
    if not to_process:
        logger.warning("transit build: no lines selected; check line_name_path/_field and companys/lines. Stopping.")
        return stats

    logger.info("transit build: %d lines in list, processing %d", len(records), len(to_process))

    for name in to_process:
        try:
            raw_entity = transit_client.get_line_entity(token, name)
            insert_transit_line_raw(engine, name, raw_entity)  # 归档原始线路对象(供 match-report 取原始轨迹)
            parsed = json.loads(raw_entity)
            tracks = parse_line_tracks(parsed)
            if not tracks:
                logger.warning("transit build: line '%s' has no directional track; skipping", name)
                stats["skipped_lines"] += 1
                continue
            for t in tracks:
                segments = converter.linetrack_to_segments(
                    t["track"],
                    passes=config.sdk.refine_passes,
                    densify_step_m=config.sdk.densify_step_m,
                )
                written = replace_transit_segments(
                    engine, t["line_name"], t["direction"], t["nor_code"], segments
                )
                stats["directions"] += 1
                stats["segments"] += written
                logger.info(
                    "transit build: line %s dir %s -> %d segments", t["line_name"], t["direction"], written
                )
            for st in parse_line_stations(parsed):
                replace_transit_stations(engine, st["line_name"], st["direction"],
                                         st["nor_code"], st["stations"])
            stats["lines"] += 1
        except Exception:  # noqa: BLE001 - one bad line must not abort the whole build
            logger.exception("transit build: line '%s' failed; skipping", name)
            stats["skipped_lines"] += 1
            continue

    if stats["lines"] and stats["segments"] == 0:
        logger.warning(
            "transit build: processed %d line(s) but produced 0 segments — is the road network "
            "(road_link_coord) loaded? run `run-once road-network` first.", stats["lines"]
        )
    try:
        sec = run_section_build(engine, config)
        stats["sections"] = sec.get("sections", 0)
    except Exception:  # noqa: BLE001 - section-build failure must not fail the whole transit build
        logger.exception("transit build: section-build step failed")
    logger.info("transit build: done %s", stats)
    return stats
