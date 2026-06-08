from sqlalchemy import Engine, inspect, text

from .schema import metadata

# Columns added after a table first shipped. create_all() only creates missing TABLES; it never
# alters an existing one, so these are applied with ADD COLUMN for already-created databases.
# (table, column, SQL type) — ADD COLUMN <col> <type> is valid on both SQLite and MySQL.
_ADDED_COLUMNS = [
    ("traffic_status", "traffic_time", "TEXT"),
]


def init_db(engine: Engine) -> None:
    """Create all tables and indexes if absent, then add any later-added columns. Idempotent."""
    metadata.create_all(engine, checkfirst=True)
    _ensure_columns(engine)


def _ensure_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    for table, column, col_type in _ADDED_COLUMNS:
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
