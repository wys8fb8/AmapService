from amap_service.config.schema import MqttConfig
from amap_service.publish.client import make_mqtt_client, NoOpMqttClient


def test_disabled_returns_noop():
    client = make_mqtt_client(MqttConfig(enabled=False))
    assert isinstance(client, NoOpMqttClient)
    client.connect()
    client.publish("t", "{}", qos=0, retain=False)
    client.disconnect()


def test_enabled_builds_paho_client_without_connecting():
    client = make_mqtt_client(MqttConfig(enabled=True, client_id="test-cli"))
    assert not isinstance(client, NoOpMqttClient)
    assert hasattr(client, "publish")
