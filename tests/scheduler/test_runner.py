from amap_service.config.schema import AppConfig
from amap_service.cache.client import NoOpCache
from amap_service.scheduler.runner import build_scheduler


def _config(**overrides):
    data = {
        "amap": {
            "endpoint": "http://192.168.102.102:8080",
            "jobs": {
                "road_network": {"path": "/road", "cron": "0 1 * * *"},
                "traffic_status": {"path": "/traffic", "cron": "*/2 * * * *"},
            },
        },
        "transit": {"username": "u", "password": "p",
                    "token_url": "http://t", "line_list_url": "http://l", "line_entity_url": "http://e"},
    }
    data.update(overrides)
    return AppConfig.model_validate(data)


def test_builds_jobs_for_enabled_amap_jobs():
    sched = build_scheduler(_config(), engine=object(), http_client=object(), cache=NoOpCache())
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"road_network", "traffic_status"}


def test_disabled_job_not_scheduled():
    cfg = _config()
    cfg.amap.jobs.traffic_status.enabled = False
    sched = build_scheduler(cfg, engine=object(), http_client=object(), cache=NoOpCache())
    assert {j.id for j in sched.get_jobs()} == {"road_network"}


def test_cron_trigger_applied():
    sched = build_scheduler(_config(), engine=object(), http_client=object(), cache=NoOpCache())
    job = sched.get_job("traffic_status")
    assert "minute='*/2'" in str(job.trigger)


def test_traffic_job_gets_on_complete_when_provided(monkeypatch):
    """build_scheduler 把 on_traffic_complete 透传给 run_traffic。"""
    import amap_service.scheduler.runner as runner

    captured = {}

    def fake_run_traffic(*args, **kwargs):
        captured["on_complete"] = kwargs.get("on_complete")
        return {"written": 0, "failed": 0}

    monkeypatch.setattr(runner, "run_traffic", fake_run_traffic)

    cfg = _config()
    sentinel = lambda rows: None
    sched = runner.build_scheduler(cfg, engine=None, http_client=None, cache=None,
                                   on_traffic_complete=sentinel)
    job = next(j for j in sched.get_jobs() if j.id == "traffic_status")
    job.func()
    assert captured["on_complete"] is sentinel


def test_traffic_run_on_start_adds_immediate_job():
    from apscheduler.triggers.date import DateTrigger
    cfg = _config()
    cfg.amap.jobs.traffic_status.run_on_start = True
    sched = build_scheduler(cfg, engine=object(), http_client=object(), cache=NoOpCache())
    ids = {j.id for j in sched.get_jobs()}
    assert "traffic_status_on_start" in ids
    assert isinstance(sched.get_job("traffic_status_on_start").trigger, DateTrigger)


def test_no_immediate_job_without_run_on_start():
    sched = build_scheduler(_config(), engine=object(), http_client=object(), cache=NoOpCache())
    assert "traffic_status_on_start" not in {j.id for j in sched.get_jobs()}


def test_run_on_start_job_passes_on_complete(monkeypatch):
    """启动即发布的一次性任务同样透传 on_complete(会触发 MQTT 发布)。"""
    import amap_service.scheduler.runner as runner
    captured = {}

    def fake_run_traffic(*args, **kwargs):
        captured["on_complete"] = kwargs.get("on_complete")
        return {"written": 0, "failed": 0}

    monkeypatch.setattr(runner, "run_traffic", fake_run_traffic)
    cfg = _config()
    cfg.amap.jobs.traffic_status.run_on_start = True
    sentinel = lambda rows: None
    sched = runner.build_scheduler(cfg, engine=None, http_client=None, cache=None,
                                   on_traffic_complete=sentinel)
    sched.get_job("traffic_status_on_start").func()
    assert captured["on_complete"] is sentinel
