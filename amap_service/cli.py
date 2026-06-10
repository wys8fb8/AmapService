"""Command-line entry: initdb / run-once <job> / run (daemon)."""
import argparse
import logging
from typing import Optional

from amap_service.cache.client import make_cache
from amap_service.clients.base import HttpClient
from amap_service.config.loader import load_config
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.clients.transit import TransitClient
from amap_service.pipelines.road_network import run_road_network
from amap_service.pipelines.transit import run_transit_stage1
from amap_service.pipelines.transit_build import run_transit_build
from amap_service.pipelines.traffic import run_traffic
from amap_service.pipelines.section_build import run_section_build
from amap_service.scheduler.runner import build_scheduler
from amap_service.publish.client import make_mqtt_client
from amap_service.publish.publisher import MqttPublisher
from amap_service.views.static_cache import StaticLineCache

logger = logging.getLogger(__name__)


def _configure_logging(config) -> None:
    logging.basicConfig(level=getattr(logging, config.logging.level.upper(), logging.INFO))


def _build(config):
    engine = make_engine(config.database)
    client = HttpClient(
        timeout_seconds=config.http.timeout_seconds,
        max_retries=config.http.max_retries,
        backoff_seconds=config.http.backoff_seconds,
        headers=config.amap.auth.headers,
    )
    cache = make_cache(config.redis)
    return engine, client, cache


def cmd_initdb(config_path: str) -> None:
    config = load_config(config_path)
    _configure_logging(config)
    init_db(make_engine(config.database))
    logger.info("initdb: tables ensured")


def cmd_run_once(config_path: str, job: str) -> dict:
    config = load_config(config_path)
    _configure_logging(config)
    engine, client, cache = _build(config)
    init_db(engine)
    amap = config.amap
    if job == "road-network":
        rn = amap.jobs.road_network
        return run_road_network(engine, client, amap.endpoint, rn.path, rn.parse_mode)
    if job == "traffic":
        ts = amap.jobs.traffic_status
        uses = config.redis.uses
        return run_traffic(
            engine, client, amap.endpoint, ts.path, ts.parse_mode,
            cache=cache, snapshot=uses.latest_traffic_snapshot, incremental=uses.incremental_detection,
            traffic_ttl_seconds=config.redis.traffic_ttl_seconds,
        )
    if job in ("transit", "transit-build"):
        tc = TransitClient(
            config.transit,
            timeout=config.http.timeout_seconds,
            cache=cache,
            token_cache_enabled=config.redis.uses.token_cache,
            line_cache_enabled=config.redis.uses.transit_line_cache,
            line_cache_expire_hour=config.transit.line_cache_expire_hour,
        )
        if job == "transit":
            return run_transit_stage1(engine, tc, config)
        return run_transit_build(engine, tc, config)
    if job == "section-build":
        return run_section_build(engine, config)
    raise SystemExit(f"unknown job: {job}")


def cmd_run(config_path: str) -> None:
    config = load_config(config_path)
    _configure_logging(config)
    engine, client, cache = _build(config)
    init_db(engine)

    on_traffic_complete = None
    if config.mqtt.enabled:
        mqtt_client = make_mqtt_client(config.mqtt)
        mqtt_client.connect()
        publisher = MqttPublisher(
            mqtt_client,
            StaticLineCache(engine, ttl_seconds=config.mqtt.static_cache_ttl_seconds),
            config.mqtt,
        )
        on_traffic_complete = publisher.publish_all
        logger.info("mqtt publisher enabled (prefix=%s)", config.mqtt.topic_prefix)

    sched = build_scheduler(config, engine, client, cache,
                            on_traffic_complete=on_traffic_complete)
    logger.info("scheduler starting with jobs: %s", [j.id for j in sched.get_jobs()])
    sched.start()


def cmd_match_report(config_path: str, output: Optional[str] = None, to_db: bool = False) -> dict:
    """统计每条已匹配线路各方向的原始轨迹长度 vs 匹配后路段长度。

    输出去向:
    - 文件：默认 logs/line_match_report.xlsx(差异 >10% 标红、5%~10% 标黄);
      -o 以 .csv 结尾则输出 CSV(无标色)。
    - 数据库：--db 写入 transit_match_report 表(整体替换),便于 SQL 查询。
    若只给 --db 而不给 -o,则只写库、不写文件。
    """
    from amap_service.reports.match_report import (
        build_match_report, write_match_report_csv, write_match_report_db, write_match_report_xlsx,
    )
    config = load_config(config_path)
    _configure_logging(config)
    engine = make_engine(config.database)
    rows = build_match_report(engine)

    # 只给 --db 时不写文件;否则写文件(默认 xlsx,.csv 结尾则 CSV)。
    file_out = output if output else (None if to_db else "logs/line_match_report.xlsx")
    if file_out:
        if file_out.lower().endswith(".csv"):
            write_match_report_csv(rows, file_out)
        else:
            write_match_report_xlsx(rows, file_out)
        logger.info("match-report: %d rows -> %s", len(rows), file_out)
        print(f"match-report: wrote {len(rows)} rows to {file_out}")
    if to_db:
        init_db(engine)  # 确保 transit_match_report 表存在
        write_match_report_db(engine, rows)
        logger.info("match-report: %d rows -> table transit_match_report", len(rows))
        print(f"match-report: wrote {len(rows)} rows to table transit_match_report")
    return {"rows": len(rows), "file": file_out, "db": to_db}


def cmd_serve(config_path: str) -> None:
    config = load_config(config_path)
    _configure_logging(config)
    if not config.api.enabled:
        raise SystemExit("api.enabled is false; refusing to serve")
    import uvicorn
    from amap_service.api.app import create_app
    app = create_app(config)
    uvicorn.run(app, host=config.api.host, port=config.api.port)


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="amap-service")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("initdb", "run", "serve"):
        sp = sub.add_parser(name)
        sp.add_argument("-c", "--config", default="config/config.yaml")
    ro = sub.add_parser("run-once")
    ro.add_argument("job", choices=["road-network", "traffic", "transit", "transit-build", "section-build"])
    ro.add_argument("-c", "--config", default="config/config.yaml")
    mr = sub.add_parser("match-report")
    mr.add_argument("-c", "--config", default="config/config.yaml")
    mr.add_argument("-o", "--output", default=None,
                    help="文件输出路径(默认 logs/line_match_report.xlsx;.csv 结尾则输出 CSV)")
    mr.add_argument("--db", action="store_true",
                    help="写入 transit_match_report 表(整体替换),便于 SQL 查询;只给 --db 则不写文件")

    args = parser.parse_args(argv)
    if args.cmd == "initdb":
        cmd_initdb(args.config)
    elif args.cmd == "run-once":
        cmd_run_once(args.config, args.job)
    elif args.cmd == "run":
        cmd_run(args.config)
    elif args.cmd == "serve":
        cmd_serve(args.config)
    elif args.cmd == "match-report":
        cmd_match_report(args.config, args.output, to_db=args.db)


if __name__ == "__main__":  # pragma: no cover
    main()
