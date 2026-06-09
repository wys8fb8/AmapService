"""查询层 SDK：按 link_id 取实时路况，先 Redis(traffic:latest:{id}) 命中、未命中回落 DB。"""
import json

from sqlalchemy import Engine, select

from amap_service.db.schema import traffic_status


def _row(d: dict) -> dict:
    return {"state": d.get("state"), "speed": d.get("speed"),
            "travel_time": d.get("travel_time"), "traffic_time": d.get("traffic_time")}


class TrafficReader:
    def __init__(self, engine: Engine, cache=None):
        self.engine = engine
        self.cache = cache

    def get_latest_traffic(self, link_ids: list) -> dict:
        """-> { link_id: {state, speed, travel_time, traffic_time} }。
        Redis 命中优先；未命中的一次 IN 查 DB 回落；两处都无的 link 不出现在结果里。"""
        ids = list(dict.fromkeys(link_ids))   # 去重保序
        if not ids:
            return {}
        result, misses = {}, ids
        if self.cache is not None and getattr(self.cache, "enabled", False):
            vals = self.cache.mget([f"traffic:latest:{i}" for i in ids])
            misses = []
            for i, v in zip(ids, vals):
                if v is None:
                    misses.append(i)
                else:
                    result[i] = _row(json.loads(v))
        if misses:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(traffic_status.c.link_id, traffic_status.c.state,
                           traffic_status.c.speed, traffic_status.c.travel_time,
                           traffic_status.c.traffic_time)
                    .where(traffic_status.c.link_id.in_(set(misses)))
                ).all()
            for r in rows:
                result[r.link_id] = {"state": r.state, "speed": r.speed,
                                     "travel_time": r.travel_time, "traffic_time": r.traffic_time}
        return result
