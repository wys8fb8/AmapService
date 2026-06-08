"""Best-effort extraction of token and line names from the (still-unknown) transit responses.

Explicit dot-path config wins; otherwise common-shape heuristics are tried. Returns None /
[] when undetermined so the stage-1 pipeline can archive raw and stop gracefully.
"""
from typing import Optional

_TOKEN_KEYS = ("token", "accessToken", "access_token", "Token", "AccessToken")
_NAME_KEYS = ("lineName", "name", "LineName", "Name", "Roadline", "Normalcode")
_CONTAINERS = ("data", "result", "Data", "Result", "lines", "list")


def _dig(obj, path: str):
    """Navigate a dot-path (e.g. 'data.token' or 'data.0.x') through nested dict/list."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def extract_token(raw, path: Optional[str] = None) -> Optional[str]:
    if path:
        val = _dig(raw, path)
        return str(val) if val is not None else None
    if isinstance(raw, dict):
        for key in _TOKEN_KEYS:
            if raw.get(key) is not None:
                return str(raw[key])
        for container in _CONTAINERS:
            sub = raw.get(container)
            if isinstance(sub, dict):
                for key in _TOKEN_KEYS:
                    if sub.get(key) is not None:
                        return str(sub[key])
    return None


def _candidate_list(raw, path: Optional[str]):
    candidate = _dig(raw, path) if path else None
    if candidate is None:
        if isinstance(raw, list):
            candidate = raw
        elif isinstance(raw, dict):
            for container in _CONTAINERS:
                if isinstance(raw.get(container), list):
                    candidate = raw[container]
                    break
    return candidate if isinstance(candidate, list) else []


def _item_name(item: dict, name_field: Optional[str]) -> Optional[str]:
    if name_field:
        value = item.get(name_field)
        return str(value) if value not in (None, "") else None
    for key in _NAME_KEYS:
        if item.get(key):
            return str(item[key])
    return None


def extract_line_names(raw, path: Optional[str] = None, name_field: Optional[str] = None) -> list:
    names = []
    for item in _candidate_list(raw, path):
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = _item_name(item, name_field)
            if name is not None:
                names.append(name)
    return names


def extract_line_records(raw, path: Optional[str] = None, name_field: Optional[str] = None,
                         company_field: str = "Company") -> list:
    """Like extract_line_names but also carries each line's company, for filtering.

    Returns [{"name": str, "company": str|None}, ...].
    """
    records = []
    for item in _candidate_list(raw, path):
        if isinstance(item, str):
            records.append({"name": item, "company": None})
        elif isinstance(item, dict):
            name = _item_name(item, name_field)
            if name is not None:
                records.append({"name": name, "company": item.get(company_field)})
    return records


def select_line_names(records: list, companys=None, lines=None, limit: int = 0) -> list:
    """Filter line records by company set / explicit line set, then cap by limit (0 = all).

    companys / lines are sets (or None to skip that filter). Order is preserved.
    """
    selected = []
    for r in records:
        if companys and r.get("company") not in companys:
            continue
        if lines and r["name"] not in lines:
            continue
        selected.append(r["name"])
    if limit and limit > 0:
        selected = selected[:limit]
    return selected


def parse_line_tracks(raw) -> list:
    """From a GetRoadLineEntity response, yield one dict per directional track.

    Real shape: {"Data": {"LineName","NorCode", "UpObject": {"UpDown":0,"LineLonLat":"lng,lat;..."},
    "DownObject": {...} | None}}. SingleLoop lines carry only UpObject. Returns
    [{line_name, nor_code, direction, track}, ...]; directions with no LineLonLat are skipped.
    """
    data = raw.get("Data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return []
    line_name = str(data.get("LineName") or "")
    nor_code = data.get("NorCode")
    tracks = []
    for obj in (data.get("UpObject"), data.get("DownObject")):
        if not isinstance(obj, dict):
            continue
        track = obj.get("LineLonLat")
        direction = obj.get("UpDown")
        if not track or direction is None:  # skip malformed direction (NOT NULL on direction)
            continue
        tracks.append({
            "line_name": line_name,
            "nor_code": nor_code,
            "direction": direction,
            "track": track,
        })
    return tracks
