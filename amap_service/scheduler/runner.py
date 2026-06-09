"""Assemble a cron-driven scheduler from config, wiring the requirement-1 pipelines."""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from amap_service.pipelines.road_network import run_road_network
from amap_service.pipelines.traffic import run_traffic

logger = logging.getLogger(__name__)


def build_scheduler(config, engine, http_client, cache) -> BlockingScheduler:
    """Register an APScheduler job per enabled amap job. Returns an unstarted scheduler."""
    sched = BlockingScheduler()
    amap = config.amap

    rn = amap.jobs.road_network
    if rn.enabled:
        sched.add_job(
            lambda: run_road_network(engine, http_client, amap.endpoint, rn.path, rn.parse_mode),
            CronTrigger.from_crontab(rn.cron),
            id="road_network", max_instances=1, coalesce=True,
        )

    ts = amap.jobs.traffic_status
    if ts.enabled:
        uses = config.redis.uses
        sched.add_job(
            lambda: run_traffic(
                engine, http_client, amap.endpoint, ts.path, ts.parse_mode,
                cache=cache, snapshot=uses.latest_traffic_snapshot, incremental=uses.incremental_detection,
                traffic_ttl_seconds=config.redis.traffic_ttl_seconds,
            ),
            CronTrigger.from_crontab(ts.cron),
            id="traffic_status", max_instances=1, coalesce=True,
        )

    return sched
