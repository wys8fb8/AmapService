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
        publisher = MqttPublisher(mqtt_client, StaticLineCache(engine), config.mqtt)
        on_traffic_complete = publisher.publish_all
        logger.info("mqtt publisher enabled (prefix=%s)", config.mqtt.topic_prefix)

    sched = build_scheduler(config, engine, client, cache,
                            on_traffic_complete=on_traffic_complete)
    logger.info("scheduler starting with jobs: %s", [j.id for j in sched.get_jobs()])
    sched.start()


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

    args = parser.parse_args(argv)
    if args.cmd == "initdb":
        cmd_initdb(args.config)
    elif args.cmd == "run-once":
        cmd_run_once(args.config, args.job)
    elif args.cmd == "run":
        cmd_run(args.config)
    elif args.cmd == "serve":
        cmd_serve(args.config)


if __name__ == "__main__":  # pragma: no cover
    main()
