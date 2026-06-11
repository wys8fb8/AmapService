"""推送 payload 编码器:把视图 dict 编码为 MQTT payload。

视图层(line_views)产出 dict 不变;本层负责 dict→bytes/str,并各自声明主题后缀。
JsonEncoder 行为与改造前 json.dumps 逐字节一致;ProtobufEncoder 惰性 import 生成模块
(json 模式不触发 protobuf 依赖)。link_id 在视图里被字符串化,这里转回 int 填 int64 字段。
"""
import json


class JsonEncoder:
    suffix = ""

    def encode_traffic(self, view: dict) -> str:
        return json.dumps(view, ensure_ascii=False)

    def encode_section(self, view: dict) -> str:
        return json.dumps(view, ensure_ascii=False)


class ProtobufEncoder:
    def __init__(self, suffix: str):
        self.suffix = suffix
        from amap_service.publish.proto import line_traffic_pb2 as pb
        self._pb = pb

    def encode_traffic(self, view: dict) -> bytes:
        pb = self._pb
        msg = pb.TrafficView(line_name=view["line_name"])
        if view.get("traffic_time") is not None:
            msg.traffic_time = view["traffic_time"]
        for d in view["directions"]:
            dr = msg.directions.add()
            dr.direction = d["direction"]
            for s in d["segments"]:
                seg = dr.segments.add()
                seg.seq = s["seq"]
                seg.link_id = int(s["link_id"])
                if s.get("state") is not None:
                    seg.state = s["state"]
                if s.get("speed") is not None:
                    seg.speed = s["speed"]
                if s.get("travel_time") is not None:
                    seg.travel_time = s["travel_time"]
                seg.reverse = s["reverse"]
                if s.get("line_track") is not None:
                    seg.line_track = s["line_track"]
        return msg.SerializeToString()

    def encode_section(self, view: dict) -> bytes:
        pb = self._pb
        msg = pb.SectionView(line_name=view["line_name"])
        if view.get("traffic_time") is not None:
            msg.traffic_time = view["traffic_time"]
        for d in view["directions"]:
            dr = msg.directions.add()
            dr.direction = d["direction"]
            for sec in d["sections"]:
                so = dr.sections.add()
                so.from_level_id = sec["from_level_id"]
                so.to_level_id = sec["to_level_id"]
                for lk in sec["links"]:
                    lo = so.links.add()
                    lo.link_id = int(lk["link_id"])
                    lo.state = lk["state"]
                    lo.pct = lk["pct"]
                    if lk.get("line_track") is not None:
                        lo.line_track = lk["line_track"]
        return msg.SerializeToString()


def build_encoders(payload_format: str, pb_suffix: str) -> list:
    encoders = []
    if payload_format in ("json", "both"):
        encoders.append(JsonEncoder())
    if payload_format in ("protobuf", "both"):
        encoders.append(ProtobufEncoder(pb_suffix))
    return encoders
