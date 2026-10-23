"""Per-node, read-only source registry for Alexandria."""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from library.db import init_library_db, library_data_dir, library_source_dir

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _globs(value: Any) -> str:
    if value is None:
        return "[]"
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("globs must be a list of non-empty strings")
    return json.dumps(value)


def _validated_root(root_path: str | Path, *, data_dir: Path | None = None) -> Path:
    if not isinstance(root_path, (str, Path)):
        raise ValueError("root_path must be a filesystem path")
    root = Path(root_path)
    if not root.is_absolute():
        raise ValueError("root_path must be absolute")
    if not root.exists() or not root.is_dir():
        raise ValueError("root_path must exist and be a directory")
    root = root.resolve()
    data = (data_dir or library_data_dir()).resolve()
    if root == data or data in root.parents:
        raise ValueError("root_path must not be inside LIBRARY_DATA_DIR")
    return root


def add_source(
    db_path: Path,
    *,
    source_id: str,
    label: str,
    root_path: str | Path,
    shared: bool = False,
    include_globs: Any = None,
    exclude_globs: Any = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    if not isinstance(source_id, str) or not _SLUG.fullmatch(source_id):
        raise ValueError("source id must be a lowercase slug")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label is required")
    if not isinstance(shared, bool):
        raise ValueError("shared must be boolean")
    root = _validated_root(root_path)
    db = init_library_db(db_path)
    includes, excludes = _globs(include_globs), _globs(exclude_globs)
    with closing(sqlite3.connect(db)) as conn:
        overlap = conn.execute("SELECT id,root_path FROM library_sources WHERE status='active'").fetchall()
        for other_id, other_path in overlap:
            other = Path(other_path).resolve()
            if root == other or root in other.parents or other in root.parents:
                raise ValueError(f"source root overlaps existing source {other_id}")
        conn.execute(
            "INSERT INTO library_sources("
            "id,label,root_path,shared,include_globs,exclude_globs,created_at,created_by,status) "
            "VALUES(?,?,?,?,?,?,?,?, 'active')",
            (
                source_id,
                label.strip(),
                str(root),
                int(shared),
                includes,
                excludes,
                datetime.now(UTC).isoformat(),
                created_by,
            ),
        )
        conn.commit()
    return get_source(db, source_id)


def seed_default_source(db_path: Path | None = None) -> None:
    root = library_source_dir()
    if root is None:
        return
    db = init_library_db(db_path)
    with closing(sqlite3.connect(db)) as conn:
        if conn.execute("SELECT 1 FROM library_sources LIMIT 1").fetchone():
            return
    try:
        add_source(db, source_id="default", label="Default", root_path=root)
    except (OSError, ValueError) as exc:
        # A legacy configured source remains usable by the ingest loop; invalid
        # paths are deliberately not registered.
        import logging

        logging.getLogger("dashboard.library.sources").warning("could not seed default library source: %s", exc)


def list_sources(db_path: Path) -> list[dict[str, Any]]:
    db = init_library_db(db_path)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM library_sources WHERE status='active' ORDER BY id").fetchall()
        counts = dict(
            conn.execute("SELECT source_id, COUNT(*) FROM files WHERE status='present' GROUP BY source_id").fetchall()
        )
    return [{**_row(row), "file_count": counts.get(row["id"], 0)} for row in rows]


def get_source(db_path: Path, source_id: str) -> dict[str, Any]:
    source = next((item for item in list_sources(db_path) if item["id"] == source_id), None)
    if source is None:
        raise LookupError(f"unknown source: {source_id}")
    return source


def update_source(db_path: Path, source_id: str, **changes: Any) -> dict[str, Any]:
    allowed = {"label", "shared", "include_globs", "exclude_globs"}
    if not changes or set(changes) - allowed:
        raise ValueError("only label, shared and globs may be updated")
    get_source(db_path, source_id)
    values: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "label":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("label is required")
            values[key] = value.strip()
        elif key == "shared":
            if not isinstance(value, bool):
                raise ValueError("shared must be boolean")
            values[key] = int(value)
        else:
            values[key] = _globs(value)
    with closing(sqlite3.connect(db_path)) as conn:
        set_clause = ",".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE library_sources SET {set_clause} WHERE id=?", (*values.values(), source_id))
        conn.commit()
    return get_source(db_path, source_id)


def remove_source(db_path: Path, source_id: str) -> None:
    db = init_library_db(db_path)
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute("SELECT 1 FROM library_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise LookupError(f"unknown source: {source_id}")
        conn.execute("DELETE FROM files WHERE source_id=?", (source_id,))
        conn.execute("DELETE FROM library_sources WHERE id=?", (source_id,))
        conn.execute(
            "UPDATE works SET status='retired' WHERE status='active' "
            "AND id NOT IN (SELECT work_id FROM files WHERE status='present')"
        )
        conn.commit()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["shared"] = bool(result["shared"])
    result["include_globs"] = json.loads(result["include_globs"])
    result["exclude_globs"] = json.loads(result["exclude_globs"])
    return result


def matches_source_globs(rel_path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
    return (not include_globs or any(fnmatch.fnmatch(rel_path, pattern) for pattern in include_globs)) and not any(
        fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_globs
    )
