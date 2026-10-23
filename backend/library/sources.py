"""Per-node and multi-backend source registry for Alexandria (#2521)."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from library.db import init_library_db, library_data_dir, library_source_dir

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_DRIVE_URL_PATTERNS = (
    re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]{10,128})"),
    re.compile(r"drive\.google\.com/open\?id=([A-Za-z0-9_-]{10,128})"),
    re.compile(r"^gdrive://([A-Za-z0-9_-]{10,128})$"),
)
BACKEND_TYPES = ("google_drive_connector", "google_drive_local", "local_folder")
_DEFAULT_PRIORITIES = {"google_drive_connector": 1, "google_drive_local": 2, "local_folder": 3}


def extract_drive_folder_id(raw: str) -> tuple[str, str]:
    """Extract and validate a canonical Google Drive folder ID and URL (DbC)."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("valid Google Drive folder ID or folder URL is required")
    for pattern in _DRIVE_URL_PATTERNS:
        match = pattern.search(text)
        if match:
            fid = match.group(1)
            return fid, f"https://drive.google.com/drive/folders/{fid}"
    if "/" not in text and "\\" not in text and ":" not in text and _DRIVE_ID_RE.fullmatch(text):
        return text, f"https://drive.google.com/drive/folders/{text}"
    raise ValueError("valid Google Drive folder ID or folder URL is required")


def _globs(value: Any) -> str:
    if value is None:
        return "[]"
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("globs/repos must be a list of non-empty strings")
    return json.dumps([item.strip() for item in value])


def _validated_root(root_path: str | Path, *, data_dir: Path | None = None) -> Path:
    if not isinstance(root_path, (str, Path)) or not str(root_path).strip():
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
    root_path: str | Path = "",
    shared: bool = False,
    include_globs: Any = None,
    exclude_globs: Any = None,
    created_by: str | None = None,
    backend_type: str = "local_folder",
    drive_folder_id: str = "",
    drive_folder_url: str = "",
    preferential: bool | None = None,
    priority: int | None = None,
    repos: Any = None,
) -> dict[str, Any]:
    if not isinstance(source_id, str) or not _SLUG.fullmatch(source_id):
        raise ValueError("source id must be a lowercase slug")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label is required")
    if not isinstance(shared, bool):
        raise ValueError("shared must be boolean")
    if backend_type not in BACKEND_TYPES:
        raise ValueError(f"backend_type must be one of {BACKEND_TYPES}")
    pref = (backend_type == "google_drive_connector") if preferential is None else preferential
    if not isinstance(pref, bool):
        raise ValueError("preferential must be boolean")
    prio = _DEFAULT_PRIORITIES[backend_type] if priority is None else priority
    if isinstance(prio, bool) or not isinstance(prio, int) or not (1 <= prio <= 99):
        raise ValueError("priority must be an integer between 1 and 99")
    includes, excludes, repos_json = _globs(include_globs), _globs(exclude_globs), _globs(repos)
    db = init_library_db(db_path)
    raw_drive = (drive_folder_id or drive_folder_url or "").strip()
    if backend_type == "google_drive_connector":
        fid, furl = extract_drive_folder_id(raw_drive or str(root_path or ""))
        root_str = f"gdrive://{fid}"
    else:
        root = _validated_root(root_path)
        root_str = str(root)
        fid, furl = extract_drive_folder_id(raw_drive) if raw_drive else ("", "")
    with closing(sqlite3.connect(db)) as conn:
        if backend_type != "google_drive_connector":
            overlap = conn.execute("SELECT id,root_path FROM library_sources WHERE status='active'").fetchall()
            for other_id, other_path in overlap:
                if str(other_path).startswith("gdrive://"):
                    continue
                other = Path(other_path).resolve()
                if root == other or root in other.parents or other in root.parents:
                    raise ValueError(f"source root overlaps existing source {other_id}")
        conn.execute(
            "INSERT INTO library_sources("
            "id,label,root_path,shared,include_globs,exclude_globs,created_at,created_by,status,"
            "backend_type,drive_folder_id,drive_folder_url,preferential,priority,repos) "
            "VALUES(?,?,?,?,?,?,?,?,'active',?,?,?,?,?,?)",
            (
                source_id,
                label.strip(),
                root_str,
                int(shared),
                includes,
                excludes,
                datetime.now(UTC).isoformat(),
                created_by,
                backend_type,
                fid,
                furl,
                int(pref),
                prio,
                repos_json,
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
        import logging

        logging.getLogger("dashboard.library.sources").warning("could not seed default library source: %s", exc)


def source_availability(root_path: str | Path, *, backend_type: str = "local_folder", drive_folder_id: str = "") -> str:
    """Report read access to a registered root or Drive connector without mutating the registry."""
    raw = str(root_path)
    if backend_type == "google_drive_connector" or raw.startswith("gdrive://"):
        fid = drive_folder_id or raw.removeprefix("gdrive://")
        return "available" if bool(_DRIVE_ID_RE.fullmatch(fid)) else "unavailable"
    root = Path(root_path)
    try:
        if root.is_symlink() or not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
            return "unavailable"
    except OSError:
        return "unavailable"
    return "available"


def list_sources(db_path: Path) -> list[dict[str, Any]]:
    db = init_library_db(db_path)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM library_sources ORDER BY preferential DESC, priority ASC, id ASC").fetchall()
        counts = dict(
            conn.execute("SELECT source_id, COUNT(*) FROM files WHERE status='present' GROUP BY source_id").fetchall()
        )
    unshared_fids = {
        str(r["drive_folder_id"])
        for r in rows
        if r["drive_folder_id"] and not bool(r["shared"]) and r["status"] == "active"
    }
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _row(row)
        fid = item["drive_folder_id"]
        if fid and fid in unshared_fids:
            item["shared"] = False
        item["file_count"] = counts.get(row["id"], 0)
        item["availability"] = source_availability(
            row["root_path"], backend_type=item["backend_type"], drive_folder_id=fid
        )
        items.append(item)
    return items


def list_shared_drive_sources(db_path: Path, *, repo: str | None = None) -> list[dict[str, Any]]:
    """Return active, shared Google Drive connector sources ordered preferentially."""
    target = (repo or "").strip().lower()
    result: list[dict[str, Any]] = []
    for src in list_sources(db_path):
        if src.get("status") != "active" or src.get("backend_type") != "google_drive_connector":
            continue
        if not src.get("shared") or src.get("availability") != "available" or not src.get("drive_folder_id"):
            continue
        repos = [r.lower() for r in src.get("repos", [])]
        if target and repos and target not in repos:
            continue
        result.append(src)
    return result


def get_source(db_path: Path, source_id: str) -> dict[str, Any]:
    source = next((item for item in list_sources(db_path) if item["id"] == source_id), None)
    if source is None:
        raise LookupError(f"unknown source: {source_id}")
    return source


def update_source(db_path: Path, source_id: str, **changes: Any) -> dict[str, Any]:
    allowed = {
        "label",
        "shared",
        "include_globs",
        "exclude_globs",
        "preferential",
        "priority",
        "repos",
        "drive_folder_id",
        "drive_folder_url",
    }
    if not changes or set(changes) - allowed:
        raise ValueError("unsupported source update fields")
    current = get_source(db_path, source_id)
    values: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "label":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("label is required")
            values[key] = value.strip()
        elif key in ("shared", "preferential"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be boolean")
            values[key] = int(value)
        elif key == "priority":
            if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 99):
                raise ValueError("priority must be an integer between 1 and 99")
            values[key] = value
        elif key in ("drive_folder_id", "drive_folder_url"):
            fid, furl = extract_drive_folder_id(str(value or ""))
            values["drive_folder_id"] = fid
            values["drive_folder_url"] = furl
            if current["backend_type"] == "google_drive_connector":
                values["root_path"] = f"gdrive://{fid}"
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
    result["preferential"] = bool(result.get("preferential", 0))
    result["priority"] = int(result.get("priority", 3))
    result["backend_type"] = result.get("backend_type") or "local_folder"
    result["drive_folder_id"] = result.get("drive_folder_id") or ""
    result["drive_folder_url"] = result.get("drive_folder_url") or ""
    result["include_globs"] = json.loads(result["include_globs"])
    result["exclude_globs"] = json.loads(result["exclude_globs"])
    result["repos"] = json.loads(result.get("repos") or "[]")
    return result


def matches_source_globs(rel_path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
    return (not include_globs or any(fnmatch.fnmatch(rel_path, pattern) for pattern in include_globs)) and not any(
        fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_globs
    )
