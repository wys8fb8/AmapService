import logging
from typing import Iterable

from sqlalchemy import Engine, Connection, delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .schema import (
    road_link, road_link_coord, traffic_status, transit_line_raw, transit_segment,
    transit_station, transit_section_link,
)

logger = logging.getLogger(__name__)


def _upsert_stmt(conn: Connection, table, values: dict, index_col, update_cols: list[str]):
    """Build a dialect-appropriate INSERT ... ON CONFLICT/DUPLICATE UPDATE statement."""
    name = conn.dialect.name
    if name == "sqlite":
        stmt = sqlite_insert(table).values(**values)
        return stmt.on_conflict_do_update(
            index_elements=[index_col],
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
    if name == "mysql":
        stmt = mysql_insert(table).values(**values)
        return stmt.on_duplicate_key_update(
            **{c: getattr(stmt.inserted, c) for c in update_cols}
        )
    raise ValueError(f"unsupported dialect for upsert: {name}")


def _batched(items: Iterable, size: int):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_road_links(engine: Engine, links: Iterable[dict], batch_size: int = 2000) -> dict:
    """Upsert road_link rows by link_id; fully replace each link's coords (delete + reinsert)."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    for batch in _batched(links, batch_size):
        ids = [l["link_id"] for l in batch]
        try:
            with engine.begin() as conn:
                existing = set(
                    conn.execute(
                        select(road_link.c.link_id).where(road_link.c.link_id.in_(ids))
                    ).scalars().all()
                )
                for link in batch:
                    _upsert_one_link(conn, link)
                unique_ids = set(ids)
                inserted = len(unique_ids - existing)
                updated = len(unique_ids) - inserted
                skipped = len(batch) - len(unique_ids)
            stats["inserted"] += inserted
            stats["updated"] += updated
            stats["skipped"] += skipped
        except Exception:
            stats["failed"] += len(batch)
            logger.exception("road_link batch failed (%d rows)", len(batch))
    return stats


def _upsert_one_link(conn: Connection, link: dict) -> None:
    stmt = _upsert_stmt(
        conn, road_link,
        values={
            "link_id": link["link_id"],
            "road_name": link.get("road_name"),
            "length": link.get("length"),
            "formway": link.get("formway"),
            "roadclass": link.get("roadclass"),
            "line_track": link.get("line_track"),
        },
        index_col=road_link.c.link_id,
        update_cols=["road_name", "length", "formway", "roadclass", "line_track"],
    )
    conn.execute(stmt)
    conn.execute(delete(road_link_coord).where(road_link_coord.c.link_id == link["link_id"]))
    coords = link.get("coords") or []
    if coords:
        conn.execute(
            road_link_coord.insert(),
            [
                {"link_id": link["link_id"], "seq": i, "longitude": lng, "latitude": lat}
                for i, (lng, lat) in enumerate(coords)
            ],
        )


def upsert_traffic_status(engine: Engine, rows: Iterable[dict], batch_size: int = 2000) -> dict:
    """Upsert traffic_status by link_id (latest-only), refreshing updated_at."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    for batch in _batched(rows, batch_size):
        ids = [r["link_id"] for r in batch]
        try:
            with engine.begin() as conn:
                existing = set(
                    conn.execute(
                        select(traffic_status.c.link_id).where(traffic_status.c.link_id.in_(ids))
                    ).scalars().all()
                )
                for row in batch:
                    stmt = _upsert_stmt(
                        conn, traffic_status,
                        values={
                            "link_id": row["link_id"],
                            "speed": row.get("speed"),
                            "state": row.get("state"),
                            "travel_time": row.get("travel_time"),
                            "traffic_time": row.get("traffic_time"),
                            "updated_at": func.current_timestamp(),
                        },
                        index_col=traffic_status.c.link_id,
                        update_cols=["speed", "state", "travel_time", "traffic_time", "updated_at"],
                    )
                    conn.execute(stmt)
                unique_ids = set(ids)
                inserted = len(unique_ids - existing)
                updated = len(unique_ids) - inserted
                skipped = len(batch) - len(unique_ids)
            stats["inserted"] += inserted
            stats["updated"] += updated
            stats["skipped"] += skipped
        except Exception:
            stats["failed"] += len(batch)
            logger.exception("traffic_status batch failed (%d rows)", len(batch))
    return stats


def insert_transit_line_raw(engine: Engine, line_name: str, raw_response) -> None:
    """Archive one upstream transit response (stage-1 capture) into transit_line_raw."""
    with engine.begin() as conn:
        conn.execute(
            transit_line_raw.insert().values(line_name=line_name, raw_response=raw_response)
        )


def replace_transit_segments(engine: Engine, line_name: str, direction, nor_code, segments) -> int:
    """Replace one line+direction's ordered segments.

    segments: ordered list of dicts {"link_id", "reverse_coords", "line_track"}. Deletes existing
    rows for (line_name, direction) then inserts the new ordered set, so a re-run is idempotent.
    Returns the number of segments written.
    """
    with engine.begin() as conn:
        conn.execute(
            delete(transit_segment).where(
                (transit_segment.c.line_name == line_name)
                & (transit_segment.c.direction == direction)
            )
        )
        rows = [
            {
                "line_name": line_name,
                "nor_code": nor_code,
                "direction": direction,
                "seq": i,
                "link_id": s["link_id"],
                "reverse_coords": 1 if s["reverse_coords"] else 0,
                "line_track": s.get("line_track"),
            }
            for i, s in enumerate(segments)
        ]
        if rows:
            conn.execute(transit_segment.insert(), rows)
    return len(rows)


def replace_transit_stations(engine: Engine, line_name: str, direction, nor_code,
                             stations: list) -> int:
    """整体替换某线路某方向的站级静态数据（先删后插，单事务）。
    stations: [{level_id, level_name, longitude, latitude}, ...]。返回写入条数。"""
    with engine.begin() as conn:
        conn.execute(
            delete(transit_station).where(
                (transit_station.c.line_name == line_name)
                & (transit_station.c.direction == direction)
            )
        )
        if not stations:
            return 0
        conn.execute(transit_station.insert(), [
            {"line_name": line_name, "nor_code": nor_code, "direction": direction,
             "level_id": s["level_id"], "level_name": s.get("level_name"),
             "longitude": s["longitude"], "latitude": s["latitude"]}
            for s in stations
        ])
        return len(stations)


def replace_transit_section_links(engine: Engine, line_name: str, direction, nor_code,
                                  rows: list) -> int:
    """整体替换某线路某方向的站间路段占比（先删后插，单事务）。
    rows: [{from_level_id, to_level_id, seq, link_id, length_m, pct}, ...]。返回写入条数。"""
    with engine.begin() as conn:
        conn.execute(
            delete(transit_section_link).where(
                (transit_section_link.c.line_name == line_name)
                & (transit_section_link.c.direction == direction)
            )
        )
        if not rows:
            return 0
        conn.execute(transit_section_link.insert(), [
            {"line_name": line_name, "nor_code": nor_code, "direction": direction,
             "from_level_id": r["from_level_id"], "to_level_id": r["to_level_id"],
             "seq": r["seq"], "link_id": r["link_id"],
             "length_m": r["length_m"], "pct": r["pct"]}
            for r in rows
        ])
        return len(rows)
