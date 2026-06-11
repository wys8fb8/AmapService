import json

from amap_service.publish.encoders import (
    JsonEncoder, ProtobufEncoder, build_encoders,
)
from amap_service.publish.proto import line_traffic_pb2 as pb

LID = 5130091959790075998  # > 2**53,验证 int64 不丢精度


def _traffic_view(state=2, geometry=False):
    seg = {"seq": 0, "link_id": str(LID), "state": state,
           "speed": 18, "travel_time": 35, "reverse": 0}
    if geometry:
        seg["line_track"] = "121.1,31.1;121.2,31.2"
    return {"line_name": "47", "traffic_time": "2026-06-09 13:02:00",
            "directions": [{"direction": 0, "segments": [seg]}]}


def _section_view():
    return {"line_name": "47", "traffic_time": "2026-06-09 13:02:00",
            "directions": [{"direction": 0, "sections": [
                {"from_level_id": 1, "to_level_id": 2,
                 "links": [{"link_id": str(LID), "state": 1, "pct": 100}]}]}]}


def test_build_encoders_modes():
    assert [type(e).__name__ for e in build_encoders("json", ".pb")] == ["JsonEncoder"]
    assert [type(e).__name__ for e in build_encoders("protobuf", ".pb")] == ["ProtobufEncoder"]
    assert [type(e).__name__ for e in build_encoders("both", ".pb")] == \
        ["JsonEncoder", "ProtobufEncoder"]


def test_json_encoder_matches_plain_dumps():
    enc = JsonEncoder()
    assert enc.suffix == ""
    view = _traffic_view()
    assert enc.encode_traffic(view) == json.dumps(view, ensure_ascii=False)


def test_protobuf_traffic_roundtrip_int64_and_optional():
    enc = ProtobufEncoder(".pb")
    assert enc.suffix == ".pb"
    raw = enc.encode_traffic(_traffic_view())
    assert isinstance(raw, bytes)
    msg = pb.TrafficView()
    msg.ParseFromString(raw)
    seg = msg.directions[0].segments[0]
    assert seg.link_id == LID          # int64 精度无损
    assert seg.state == 2 and seg.speed == 18 and seg.travel_time == 35
    assert msg.line_name == "47"


def test_protobuf_traffic_missing_state_is_absent():
    raw = ProtobufEncoder(".pb").encode_traffic(_traffic_view(state=None))
    msg = pb.TrafficView()
    msg.ParseFromString(raw)
    seg = msg.directions[0].segments[0]
    assert seg.HasField("state") is False   # None → 不 set(区别于真值 0)


def test_protobuf_geometry_toggle():
    msg = pb.TrafficView()
    msg.ParseFromString(ProtobufEncoder(".pb").encode_traffic(_traffic_view(geometry=False)))
    assert msg.directions[0].segments[0].HasField("line_track") is False
    msg2 = pb.TrafficView()
    msg2.ParseFromString(ProtobufEncoder(".pb").encode_traffic(_traffic_view(geometry=True)))
    assert msg2.directions[0].segments[0].line_track == "121.1,31.1;121.2,31.2"


def test_protobuf_section_roundtrip():
    raw = ProtobufEncoder(".pb").encode_section(_section_view())
    msg = pb.SectionView()
    msg.ParseFromString(raw)
    sec = msg.directions[0].sections[0]
    assert sec.from_level_id == 1 and sec.to_level_id == 2
    lk = sec.links[0]
    assert lk.link_id == LID and lk.state == 1 and lk.pct == 100
