"""Requirement-2 STAGE 1: walk token → line list → line entities, archiving every raw response.

Field mapping (→ ordered segments via the requirement-3 SDK) is STAGE 2, done once the user
returns the real response structures. Stage 1 degrades gracefully when token / line names
cannot be extracted: it archives what it has and stops without error.
"""
import json
import logging
import time
from pathlib import Path

from sqlalchemy import Engine

from amap_service.clients.transit import TransitClient
from amap_service.db.repositories import insert_transit_line_raw
from amap_service.parsing.transit import extract_line_records, select_line_names

logger = logging.getLogger(__name__)


def _archive(engine: Engine, out_dir: str, file_label: str, raw_text, ts: int,
             line_name: str = None) -> None:
    """归档原始响应：磁盘文件名用 file_label（如 line_entity_47），DB line_name 用裸线路号。

    line_name 缺省时回退用 file_label（token / line_list 这类非线路响应即用其标签）。
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe = file_label.replace("/", "_").replace("\\", "_")
    safe = safe.encode("utf-8")[:150].decode("utf-8", "ignore")  # keep filename within FS limits
    (directory / f"{safe}_{ts}.json").write_text(raw_text or "", encoding="utf-8")
    insert_transit_line_raw(engine, file_label if line_name is None else line_name, raw_text)


def run_transit_stage1(engine: Engine, transit_client: TransitClient, config,
                       out_dir: str = "logs/transit_raw", now_ms=None) -> dict:
    now = now_ms or (lambda: int(time.time() * 1000))
    ts = now()
    stats = {"token_ok": False, "line_count": 0, "entities_archived": 0}

    token, raw_token = transit_client.get_token()
    if raw_token is not None:
        _archive(engine, out_dir, "token", raw_token, ts)
    if not token:
        logger.warning(
            "transit stage1: token not extracted; set transit.token_path after inspecting the "
            "archived token response. Stopping after token archival."
        )
        return stats
    stats["token_ok"] = True

    raw_list = transit_client.get_line_list(token)
    _archive(engine, out_dir, "line_list", raw_list, ts)
    t = config.transit
    try:
        records = extract_line_records(
            json.loads(raw_list), t.line_name_path, t.line_name_field, t.company_field
        )
    except Exception:  # noqa: BLE001 - unknown body may not be JSON
        records = []
    to_fetch = select_line_names(records, t.companys_set(), t.lines_set(), t.line_limit)
    stats["line_count"] = len(records)
    if not to_fetch:
        logger.warning(
            "transit stage1: no lines selected; check transit.line_name_path/_field and the "
            "companys/lines filters. token + line_list archived; stopping."
        )
        return stats

    logger.info("transit stage1: %d lines in list, %d selected for fetch", len(records), len(to_fetch))
    for name in to_fetch:
        try:
            raw_entity = transit_client.get_line_entity(token, name)
            _archive(engine, out_dir, f"line_entity_{name}", raw_entity, ts, line_name=name)
            stats["entities_archived"] += 1
        except Exception:  # noqa: BLE001 - one bad line must not abort the capture run
            logger.exception("transit stage1: line entity '%s' failed; skipping", name)
            continue

    logger.info("transit stage1: archived token + line_list + %d entities", stats["entities_archived"])
    return stats
