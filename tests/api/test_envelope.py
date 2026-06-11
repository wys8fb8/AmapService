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


from fastapi.testclient import TestClient

from amap_service.api.app import create_app
from tests.api.test_app import _base_config
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db


def test_error_404_enveloped(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/999/segments")
    body = r.json()
    assert r.status_code == 404
    assert body["success"] is False and body["code"] == 404
    assert body["data"] is None
    assert "999" in body["message"]
    assert body["requestid"].startswith("req_")
    assert TS_RE.match(body["timestamp"])


def test_error_401_enveloped(tmp_path):
    r = _client(tmp_path, auth_enabled=True).get("/api/v1/lines")
    body = r.json()
    assert r.status_code == 401 and body["code"] == 401 and body["success"] is False


def test_error_422_enveloped(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/segments?direction=5")
    body = r.json()
    assert r.status_code == 422 and body["code"] == 422
    assert body["data"] is None and "direction" in body["message"]


def test_error_500_enveloped(tmp_path):
    db_path = str(tmp_path / "t.db")
    cfg = _base_config(db_path)
    eng = make_engine(cfg.database)
    init_db(eng)
    app = create_app(cfg, engine=eng)

    @app.get("/api/v1/_boom")
    def _boom():
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/_boom")
    body = r.json()
    assert r.status_code == 500 and body["code"] == 500
    assert body["success"] is False and body["data"] is None
    assert body["message"] == "Internal Server Error"
