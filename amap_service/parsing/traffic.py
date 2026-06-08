"""Parse traffic/status into repository-ready rows, aggregating segmented links."""
import datetime
from typing import Iterator, Optional

# Amap's top-level `utcSeconds` is Unix epoch seconds; 路况时间 is presented in China time (UTC+8).
_TRAFFIC_TZ = datetime.timezone(datetime.timedelta(hours=8))


def format_traffic_time(utc_seconds) -> Optional[str]:
    """Unix epoch seconds → "yyyy-MM-dd HH:mm:ss" (UTC+8); None if missing/invalid."""
    if utc_seconds is None:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(int(utc_seconds), _TRAFFIC_TZ)
    except (ValueError, TypeError, OSError, OverflowError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _as_int(value):
    """Coerce a numeric value (int/float/Decimal) to int; pass None through."""
    if value is None:
        return None
    return int(round(value))


def _aggregate_sections(sections: list[dict]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    weighted = [(s["speed"], s.get("travelTime") or 0) for s in sections if s.get("speed") is not None]
    tt_sum = sum(tt for _, tt in weighted)
    if tt_sum > 0:
        speed: Optional[int] = round(sum(sp * tt for sp, tt in weighted) / tt_sum)
    elif weighted:
        speed = round(sum(sp for sp, _ in weighted) / len(weighted))
    else:
        speed = None

    states = [s.get("state") for s in sections if s.get("state") is not None]
    congested = [st for st in states if st != 5]  # 5 = unknown, lowest priority
    if congested:
        state: Optional[int] = max(congested)
    elif states:
        state = 5
    else:
        state = None

    travel_time = sum(s.get("travelTime") or 0 for s in sections) or None
    return speed, state, travel_time


def parse_traffic_item(item: dict, traffic_time: Optional[str] = None) -> dict:
    """One linkStates element → {link_id, speed, state, travel_time, traffic_time} (sections aggregated).

    `traffic_time` is the response-wide 路况时间 (from top-level utcSeconds); it is the same for
    every link in a fetch, so the caller passes it in.
    """
    sections = item.get("listSectionStatus")
    if sections:
        speed, state, travel_time = _aggregate_sections(sections)
    else:
        speed = item.get("speed")
        state = item.get("state")
        travel_time = item.get("travelTime")
    return {
        "link_id": item["linkId"],
        "speed": _as_int(speed),
        "state": _as_int(state),
        "travel_time": _as_int(travel_time),
        "traffic_time": traffic_time,
    }


def parse_traffic(payload: dict) -> Iterator[dict]:
    traffic_time = format_traffic_time(payload.get("utcSeconds"))
    for item in payload.get("linkStates", []):
        yield parse_traffic_item(item, traffic_time)
