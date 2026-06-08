from sqlalchemy import inspect
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db

def test_init_db_idempotent(tmp_path):
    cfg = DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db")))
    engine = make_engine(cfg)
    init_db(engine)
    init_db(engine)  # second call must not raise
    tables = set(inspect(engine).get_table_names())
    assert {"road_link", "road_link_coord", "traffic_status", "transit_line_raw"} <= tables
