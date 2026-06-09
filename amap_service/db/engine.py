from sqlalchemy import Engine, create_engine, event

from amap_service.config.schema import DatabaseConfig


def build_url(db: DatabaseConfig) -> str:
    if db.type == "sqlite":
        return f"sqlite:///{db.sqlite.path}"
    if db.type == "mysql":
        m = db.mysql
        return (
            f"mysql+pymysql://{m.user}:{m.password}@{m.host}:{m.port}/{m.database}"
            f"?charset={m.charset}"
        )
    raise ValueError(f"unsupported database type: {db.type}")


def _apply_sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA cache_size=-65536")  # ~64MB page cache
    cur.close()


def make_engine(db: DatabaseConfig) -> Engine:
    engine = create_engine(build_url(db), future=True)
    if db.type == "sqlite":
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine
