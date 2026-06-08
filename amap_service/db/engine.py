from sqlalchemy import Engine, create_engine

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


def make_engine(db: DatabaseConfig) -> Engine:
    return create_engine(build_url(db), future=True)
