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
    import sqlite3

    cur = dbapi_conn.cursor()
    # 先设 busy_timeout: 服务层(API)与 daemon(写入)同时打开同一 SQLite 时,
    # 让取锁阻塞等待而非立即报 "database is locked"。
    cur.execute("PRAGMA busy_timeout=5000")
    # journal_mode=WAL 是 DB 级持久设置,改它需短暂独占锁。第二个进程(API)连上时
    # daemon 可能正持锁——但 WAL 早已被写持久化在库头,本连接无需再设,取锁失败可安全跳过。
    try:
        cur.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA cache_size=-65536")  # ~64MB page cache
    cur.close()


def make_engine(db: DatabaseConfig) -> Engine:
    engine = create_engine(build_url(db), future=True)
    if db.type == "sqlite":
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine
