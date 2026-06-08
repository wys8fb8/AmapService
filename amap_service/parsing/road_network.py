"""Parse areaLinkPub (full road network) into repository-ready link dicts.

Works for both memory mode (json floats) and stream mode (ijson Decimals):
coordinates are normalized to float so SQLite can bind them.
"""
from typing import Iterator


def parse_road_link_item(item: dict) -> dict:
    """One linkCoordList element → {link_id, road_name, length, formway, roadclass, line_track, coords}."""
    flat = item.get("coordList") or []
    pair_count = len(flat) // 2
    coords = [(float(flat[2 * i]), float(flat[2 * i + 1])) for i in range(pair_count)]
    line_track = ";".join(f"{lng},{lat}" for lng, lat in coords)
    return {
        "link_id": item["linkId"],
        "road_name": item.get("roadName"),
        "length": item.get("length"),
        "formway": item.get("formway"),
        "roadclass": item.get("roadclass"),
        "line_track": line_track,
        "coords": coords,
    }


def parse_road_network(payload: dict) -> Iterator[dict]:
    """Map every linkCoordList element through parse_road_link_item (memory mode)."""
    for item in payload.get("linkCoordList", []):
        yield parse_road_link_item(item)
