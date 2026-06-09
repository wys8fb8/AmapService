"""MQTT 客户端封装。禁用时用 NoOpMqttClient(连 paho 都不 import)。

publish-only(不订阅、不需回调),用 paho-mqtt 1.x 简单 API。所有网络异常由调用方(发布器)捕获。
"""
import logging

logger = logging.getLogger(__name__)


class NoOpMqttClient:
    def connect(self) -> None:
        pass

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        pass

    def disconnect(self) -> None:
        pass


class PahoMqttClient:
    def __init__(self, cfg):
        import paho.mqtt.client as mqtt
        self._cfg = cfg
        self._client = mqtt.Client(client_id=cfg.client_id)
        if cfg.username:
            self._client.username_pw_set(cfg.username, cfg.password)
        self._connected = False

    def connect(self) -> None:
        self._client.connect(self._cfg.host, self._cfg.port,
                             keepalive=max(self._cfg.connect_timeout_seconds * 3, 30))
        self._client.loop_start()
        self._connected = True

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        if not self._connected:
            self.connect()
        self._client.publish(topic, payload, qos=qos, retain=retain)

    def disconnect(self) -> None:
        if self._connected:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False


def make_mqtt_client(cfg):
    if not cfg.enabled:
        return NoOpMqttClient()
    return PahoMqttClient(cfg)
