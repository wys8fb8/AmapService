from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_line_raw
from amap_service.db.repositories import insert_transit_line_raw


def test_insert_transit_line_raw(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    insert_transit_line_raw(e, "token", '{"data":{"token":"T"}}')
    insert_transit_line_raw(e, "line_list", '{"data":[]}')
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 2
        row = c.execute(
            select(transit_line_raw.c.line_name, transit_line_raw.c.raw_response)
            .where(transit_line_raw.c.line_name == "token")
        ).one()
        assert row.line_name == "token" and "T" in row.raw_response
