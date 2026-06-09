"""推/拉共用的线路视图组装。link_id 一律字符串化(防 JS 朴素解析对 >2^53 损精度)。

traffic_lookup 只需实现 get_latest_traffic(link_ids)->{link_id:{state,speed,travel_time,traffic_time}}:
发布器传 DictTrafficLookup(内存全量),API 传 TrafficReader(Redis→DB)。
线路无相关静态数据时返回 None,调用方据此 404 或跳过。
"""


def _dirs(struct: dict, direction):
    if direction is None:
        return sorted(struct)
    return [direction] if direction in struct else []


def _first_traffic_time(traffic: dict):
    for v in traffic.values():
        if v.get("traffic_time"):
            return v["traffic_time"]
    return None


def build_segment_view(cache, line_name: str, direction=None):
    segs = cache.segments(line_name)
    dirs = _dirs(segs, direction)
    if not dirs:
        return None
    return {
        "line_name": line_name,
        "directions": [
            {"direction": d,
             "segments": [
                 {"seq": s["seq"], "link_id": str(s["link_id"]),
                  "reverse": s["reverse"], "line_track": s["line_track"]}
                 for s in segs[d]]}
            for d in dirs],
    }


def build_traffic_view(cache, traffic_lookup, line_name: str, direction=None, geometry=False):
    segs = cache.segments(line_name)
    dirs = _dirs(segs, direction)
    if not dirs:
        return None
    ids = [s["link_id"] for d in dirs for s in segs[d]]
    traffic = traffic_lookup.get_latest_traffic(ids)
    out_dirs = []
    for d in dirs:
        seg_out = []
        for s in segs[d]:
            t = traffic.get(s["link_id"]) or {}
            item = {"seq": s["seq"], "link_id": str(s["link_id"]),
                    "state": t.get("state"), "speed": t.get("speed"),
                    "travel_time": t.get("travel_time"), "reverse": s["reverse"]}
            if geometry:
                item["line_track"] = s["line_track"]
            seg_out.append(item)
        out_dirs.append({"direction": d, "segments": seg_out})
    return {"line_name": line_name, "traffic_time": _first_traffic_time(traffic),
            "directions": out_dirs}


def _section_links(cache, links, traffic, geometry, default_state):
    out = []
    for lk in links:
        t = traffic.get(lk["link_id"]) or {}
        state = t.get("state")
        item = {"link_id": str(lk["link_id"]),
                "state": state if state is not None else default_state,
                "pct": lk["pct"]}
        if geometry:
            item["line_track"] = cache.link_track(lk["link_id"])
        out.append(item)
    return out


def build_section_view(cache, traffic_lookup, line_name: str, direction=None,
                       geometry=False, default_state: int = 1):
    secs = cache.sections(line_name)
    dirs = _dirs(secs, direction)
    if not dirs:
        return None
    ids = [lk["link_id"] for d in dirs for sec in secs[d] for lk in sec["links"]]
    traffic = traffic_lookup.get_latest_traffic(ids)
    out_dirs = []
    for d in dirs:
        sec_out = [
            {"from_level_id": sec["from_level_id"], "to_level_id": sec["to_level_id"],
             "links": _section_links(cache, sec["links"], traffic, geometry, default_state)}
            for sec in secs[d]]
        out_dirs.append({"direction": d, "sections": sec_out})
    return {"line_name": line_name, "traffic_time": _first_traffic_time(traffic),
            "directions": out_dirs}


def build_station_section_view(cache, traffic_lookup, line_name: str, direction: int,
                               to_level_id: int, geometry=False, default_state: int = 1):
    secs = cache.sections(line_name)
    if direction not in secs:
        return None
    matched = [sec for sec in secs[direction] if sec["to_level_id"] == to_level_id]
    if not matched:
        return None
    ids = [lk["link_id"] for sec in matched for lk in sec["links"]]
    traffic = traffic_lookup.get_latest_traffic(ids)
    links = []
    for sec in matched:
        links.extend(_section_links(cache, sec["links"], traffic, geometry, default_state))
    return {"line_name": line_name, "direction": direction, "to_level_id": to_level_id,
            "traffic_time": _first_traffic_time(traffic), "links": links}
