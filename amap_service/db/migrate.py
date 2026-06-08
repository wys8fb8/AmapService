from sqlalchemy import Engine

from .schema import metadata


def init_db(engine: Engine) -> None:
    """Create all tables and indexes if absent. Idempotent."""
    metadata.create_all(engine, checkfirst=True)
