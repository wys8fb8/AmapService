from sqlalchemy import inspect
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_new_tables_created(tmp_path):
    e = _engine(tmp_path)
    names = set(inspect(e).get_table_names())
    assert {"transit_station", "transit_section_link"} <= names


def test_transit_station_columns(tmp_path):
    e = _engine(tmp_path)
    cols = {c["name"] for c in inspect(e).get_columns("transit_station")}
    assert {"line_name", "nor_code", "direction", "level_id",
            "level_name", "longitude", "latitude", "created_at"} <= cols


def test_transit_section_link_columns(tmp_path):
    e = _engine(tmp_path)
    cols = {c["name"] for c in inspect(e).get_columns("transit_section_link")}
    assert {"line_name", "nor_code", "direction", "from_level_id", "to_level_id",
            "seq", "link_id", "length_m", "pct", "built_at"} <= cols
