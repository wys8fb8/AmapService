import textwrap
from amap_service.config.loader import load_config

YAML = textwrap.dedent(
    """
    amap:
      endpoint: "http://192.168.102.102:8080"
      jobs:
        road_network: {path: "/g5_server/map/api/areaLinkPub", cron: "0 1 * * *"}
        traffic_status: {path: "/g5_server/map/api/traffic/status", cron: "*/2 * * * *"}
    transit:
      username: "yangs"
      password: "pw"
      token_url: "http://t"
      line_list_url: "http://l"
      line_entity_url: "http://e"
    redis:
      enabled: true
      port: 6379
    """
)

def _write(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p

def test_loads_yaml(tmp_path):
    cfg = load_config(_write(tmp_path), environ={})
    assert cfg.amap.endpoint == "http://192.168.102.102:8080"
    assert cfg.amap.jobs.traffic_status.cron == "*/2 * * * *"
    assert cfg.database.type == "sqlite"

def test_env_override_scalar_and_coercion(tmp_path):
    env = {"AMAP__REDIS__PORT": "6380", "AMAP__TRANSIT__PASSWORD": "secret"}
    cfg = load_config(_write(tmp_path), environ=env)
    assert cfg.redis.port == 6380          # coerced str -> int by pydantic
    assert cfg.transit.password == "secret"

def test_env_override_ignores_unrelated_keys(tmp_path):
    env = {"PATH": "/usr/bin", "HOME": "/root"}
    cfg = load_config(_write(tmp_path), environ=env)
    assert cfg.transit.password == "pw"
