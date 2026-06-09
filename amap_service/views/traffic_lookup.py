"""把内存里的全量路况 rows 包成与 TrafficReader 同接口(get_latest_traffic)的查询器。

发布器用它(零回读);API 用 TrafficReader(Redis→DB)。视图层只依赖这个共同接口。
"""


class DictTrafficLookup:
    def __init__(self, rows):
        self._by_id = {r["link_id"]: r for r in rows}

    def get_latest_traffic(self, link_ids: list) -> dict:
        out = {}
        for i in link_ids:
            r = self._by_id.get(i)
            if r is not None:
                out[i] = {"state": r.get("state"), "speed": r.get("speed"),
                          "travel_time": r.get("travel_time"),
                          "traffic_time": r.get("traffic_time")}
        return out
