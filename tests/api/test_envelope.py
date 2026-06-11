import re

from amap_service.api.envelope import Envelope, now_iso_millis, success
from tests.api.test_app import _client

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class _StubState:
    request_id = "req_test123"


class _StubRequest:
    state = _StubState()


def test_now_iso_millis_format():
    assert TS_RE.match(now_iso_millis())


def test_success_builds_envelope():
    env = success({"x": 1}, _StubRequest())
    assert isinstance(env, Envelope)
    assert env.success is True
    assert env.code == 200
    assert env.message == "OK"
    assert env.data == {"x": 1}
    assert env.requestid == "req_test123"
    assert TS_RE.match(env.timestamp)


def test_success_custom_message_and_code():
    env = success(None, _StubRequest(), message="created", code=201)
    assert env.code == 201 and env.message == "created" and env.success is True


def test_request_id_generated_and_echoed(tmp_path):
    r = _client(tmp_path).get("/api/v1/health")
    rid = r.headers.get("X-Request-ID")
    assert rid is not None and rid.startswith("req_")


def test_request_id_passthrough(tmp_path):
    r = _client(tmp_path).get("/api/v1/health", headers={"X-Request-ID": "req_caller99"})
    assert r.headers["X-Request-ID"] == "req_caller99"
