"""Assemble a cron-driven scheduler from config, wiring the requirement-1 pipelines."""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from amap_service.pipelines.road_network import run_road_network
from amap_service.pipelines.traffic import run_traffic

logger = logging.getLogger(__name__)


def build_scheduler(config, engine, http_client, cache, on_traffic_complete=None) -> BlockingScheduler:
    """Register an APScheduler job per enabled amap job. Returns an unstarted scheduler.

    on_traffic_complete: 可选回调,在每轮路况落地后收到全量 rows(供 MQTT 发布)。
    job.run_on_start=True 时,除 cron 外再加一个一次性 DateTrigger 任务(id 为 <job>_on_start),
    scheduler 启动即跑一次,便于服务重启后立即落地/发布,而不必等下一个 cron tick。
    """
    sched = BlockingScheduler()
    amap = config.amap

    rn = amap.jobs.road_network
    if rn.enabled:
        def road_job():
            return run_road_network(engine, http_client, amap.endpoint, rn.path, rn.parse_mode)
        sched.add_job(road_job, CronTrigger.from_crontab(rn.cron),
                      id="road_network", max_instances=1, coalesce=True)
        if rn.run_on_start:
            sched.add_job(road_job, DateTrigger(),
                          id="road_network_on_start", max_instances=1, coalesce=True)

    ts = amap.jobs.traffic_status
    if ts.enabled:
        uses = config.redis.uses

        def traffic_job():
            return run_traffic(
                engine, http_client, amap.endpoint, ts.path, ts.parse_mode,
                cache=cache, snapshot=uses.latest_traffic_snapshot,
                incremental=uses.incremental_detection,
                traffic_ttl_seconds=config.redis.traffic_ttl_seconds,
                on_complete=on_traffic_complete,
            )
        sched.add_job(traffic_job, CronTrigger.from_crontab(ts.cron),
                      id="traffic_status", max_instances=1, coalesce=True)
        if ts.run_on_start:
            sched.add_job(traffic_job, DateTrigger(),
                          id="traffic_status_on_start", max_instances=1, coalesce=True)

    return sched
