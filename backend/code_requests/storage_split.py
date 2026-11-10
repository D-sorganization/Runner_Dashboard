"""Split legacy combined ``code_requests.json`` into cache vs dispatch history (#4262)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config_schema import atomic_write_json

log = logging.getLogger("dashboard.code_requests.storage_split")

_SPLIT_MARKER = ".code_requests_storage_split_v1"


def _is_dispatch_record(entry: dict[str, Any]) -> bool:
    status = str(entry.get("status") or "")
    if status not in {"dispatched", "failed"}:
        return False
    if "provider" not in entry:
        return False
    return "state" not in entry and "requester" not in entry


def _is_code_request_record(entry: dict[str, Any]) -> bool:
    if not isinstance(entry.get("id"), str):
        return False
    if "requester" in entry or "state" in entry:
        return True
    return entry["id"].startswith("cr-")


def migrate_combined_storage_if_needed(cache_path: Path, dispatch_path: Path) -> None:
    """One-time migration: move dispatch rows out of the Code Request cache file."""
    marker = cache_path.parent / _SPLIT_MARKER
    if marker.exists():
        return
    if not cache_path.exists():
        marker.write_text("ok\n", encoding="utf-8")
        return
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Skipping code_requests split migration: %s", exc)
        marker.write_text("ok\n", encoding="utf-8")
        return
    if not isinstance(raw, list):
        marker.write_text("ok\n", encoding="utf-8")
        return

    cache_rows: list[Any] = []
    dispatch_rows: list[Any] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if _is_dispatch_record(item):
            dispatch_rows.append(item)
        elif _is_code_request_record(item):
            cache_rows.append(item)
        else:
            cache_rows.append(item)

    if dispatch_rows:
        existing_dispatch: list[Any] = []
        if dispatch_path.exists():
            try:
                loaded = json.loads(dispatch_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing_dispatch = loaded
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                existing_dispatch = []
        seen = {json.dumps(row, sort_keys=True) for row in existing_dispatch}
        merged = list(existing_dispatch)
        for row in dispatch_rows:
            key = json.dumps(row, sort_keys=True)
            if key not in seen:
                merged.append(row)
                seen.add(key)
        atomic_write_json(dispatch_path, merged)

    if dispatch_rows or cache_rows != raw:
        atomic_write_json(cache_path, cache_rows)

    marker.write_text("ok\n", encoding="utf-8")
