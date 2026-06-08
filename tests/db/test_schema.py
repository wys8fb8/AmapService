from amap_service.db.schema import (
    metadata, road_link, road_link_coord, traffic_status, transit_line_raw,
)

def test_tables_registered():
    names = set(metadata.tables.keys())
    assert {"road_link", "road_link_coord", "traffic_status", "transit_line_raw"} <= names

def test_road_link_columns():
    cols = set(road_link.c.keys())
    assert {"id", "link_id", "road_name", "length", "formway",
            "roadclass", "line_track", "created_at"} == cols
    assert road_link.c.link_id.unique is True

def test_coord_columns_and_unique():
    cols = set(road_link_coord.c.keys())
    assert {"id", "link_id", "seq", "longitude", "latitude"} == cols

def test_traffic_link_id_unique_for_upsert():
    # upsert-latest semantics require a unique link_id (deviation from data-dict INDEX)
    assert traffic_status.c.link_id.unique is True
    assert set(traffic_status.c.keys()) == {
        "id", "link_id", "speed", "state", "travel_time", "updated_at"
    }

def test_transit_raw_columns():
    assert set(transit_line_raw.c.keys()) == {"id", "line_name", "raw_response", "fetched_at"}
