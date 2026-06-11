import pytest
from pydantic import ValidationError
from amap_service.config.schema import AppConfig

def _minimal():
    return {
        "amap": {
            "endpoint": "http://192.168.102.102:8080",
            "jobs": {
                "road_network": {"path": "/g5_server/map/api/areaLinkPub", "cron": "0 1 * * *"},
                "traffic_status": {"path": "/g5_server/map/api/traffic/status", "cron": "*/2 * * * *"},
            },
        },
        "transit": {
            "username": "yangs", "password": "pw",
            "token_url": "http://t", "line_list_url": "http://l", "line_entity_url": "http://e",
        },
    }

def test_defaults_applied():
    cfg = AppConfig.model_validate(_minimal())
    assert cfg.database.type == "sqlite"
    assert cfg.database.sqlite.path == "./road_network.db"
    assert cfg.redis.enabled is False
    assert cfg.redis.uses.token_cache is True
    assert cfg.http.max_retries == 3
    assert cfg.sdk.match_tolerance_m == 30
    assert cfg.amap.auth.type == "none"

def test_transit_line_cache_config_defaults():
    from amap_service.config.schema import RedisUses, TransitConfig
    assert RedisUses().transit_line_cache is True
    tc = TransitConfig(username="u", password="p", token_url="a",
                       line_list_url="b", line_entity_url="c")
    assert tc.line_cache_expire_hour == 1


def test_invalid_cron_rejected():
    data = _minimal()
    data["amap"]["jobs"]["road_network"]["cron"] = "not a cron"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)

def test_mysql_requires_block():
    data = _minimal()
    data["database"] = {"type": "mysql"}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)

def test_invalid_db_type_rejected():
    data = _minimal()
    data["database"] = {"type": "postgres"}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_transit_company_and_line_filters():
    data = _minimal()
    data["transit"]["companys"] = "巴士一公司, 巴士二公司 ,"
    data["transit"]["lines"] = "47,192"
    cfg = AppConfig.model_validate(data)
    assert cfg.transit.companys_set() == {"巴士一公司", "巴士二公司"}   # trimmed, blanks dropped
    assert cfg.transit.lines_set() == {"47", "192"}
    base = AppConfig.model_validate(_minimal())
    assert base.transit.companys_set() is None and base.transit.lines_set() is None
    assert base.transit.company_field == "Company"


def test_job_parse_mode_default_and_values():
    cfg = AppConfig.model_validate(_minimal())
    assert cfg.amap.jobs.road_network.parse_mode == "memory"

    data = _minimal()
    data["amap"]["jobs"]["road_network"]["parse_mode"] = "stream"
    cfg2 = AppConfig.model_validate(data)
    assert cfg2.amap.jobs.road_network.parse_mode == "stream"


def test_job_parse_mode_invalid_rejected():
    import pytest
    from pydantic import ValidationError
    data = _minimal()
    data["amap"]["jobs"]["traffic_status"]["parse_mode"] = "nope"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_transit_stage1_fields_defaults_and_override():
    cfg = AppConfig.model_validate(_minimal())
    assert cfg.transit.token_path is None
    assert cfg.transit.line_name_path is None
    assert cfg.transit.token_ttl_seconds == 3600

    data = _minimal()
    data["transit"]["token_path"] = "data.token"
    data["transit"]["line_name_path"] = "data"
    data["transit"]["token_ttl_seconds"] = 120
    cfg2 = AppConfig.model_validate(data)
    assert cfg2.transit.token_path == "data.token"
    assert cfg2.transit.line_name_path == "data"
    assert cfg2.transit.token_ttl_seconds == 120


def test_api_mqtt_defaults():
    from amap_service.config.schema import AppConfig
    cfg = AppConfig.model_validate({
        "amap": {"endpoint": "http://x", "jobs": {
            "road_network": {"path": "/r", "cron": "0 1 * * *"},
            "traffic_status": {"path": "/t", "cron": "*/2 * * * *"}}},
        "transit": {"username": "u", "password": "p",
                    "token_url": "http://a", "line_list_url": "http://b",
                    "line_entity_url": "http://c"},
    })
    assert cfg.api.enabled is False
    assert cfg.api.port == 8080
    assert cfg.api.auth.enabled is False
    assert cfg.api.auth.header == "X-API-Key"
    assert cfg.api.static_cache_ttl_seconds == 300
    assert cfg.mqtt.enabled is False
    assert cfg.mqtt.topic_prefix == "amap"
    assert cfg.mqtt.qos == 0
    assert cfg.mqtt.retain is False
    assert cfg.mqtt.include_geometry is False
    assert cfg.mqtt.publish_map is True
    assert cfg.mqtt.publish_section is True
    assert cfg.mqtt.static_cache_ttl_seconds == 600


def test_mqtt_qos_range_validated():
    import pytest
    from pydantic import ValidationError
    from amap_service.config.schema import MqttConfig
    with pytest.raises(ValidationError):
        MqttConfig(qos=3)


def test_mqtt_payload_format_defaults():
    from amap_service.config.schema import MqttConfig
    cfg = MqttConfig()
    assert cfg.payload_format == "json"
    assert cfg.pb_topic_suffix == ".pb"


def test_mqtt_payload_format_accepts_protobuf_and_both():
    from amap_service.config.schema import MqttConfig
    assert MqttConfig(payload_format="protobuf").payload_format == "protobuf"
    assert MqttConfig(payload_format="both").payload_format == "both"


def test_mqtt_payload_format_invalid_rejected():
    import pytest
    from pydantic import ValidationError
    from amap_service.config.schema import MqttConfig
    with pytest.raises(ValidationError):
        MqttConfig(payload_format="msgpack")


def test_job_run_on_start_default_and_override():
    cfg = AppConfig.model_validate(_minimal())
    assert cfg.amap.jobs.traffic_status.run_on_start is False
    assert cfg.amap.jobs.road_network.run_on_start is False
    data = _minimal()
    data["amap"]["jobs"]["traffic_status"]["run_on_start"] = True
    cfg2 = AppConfig.model_validate(data)
    assert cfg2.amap.jobs.traffic_status.run_on_start is True
