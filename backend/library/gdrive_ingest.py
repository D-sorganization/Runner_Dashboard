"""Google Drive (gdrive://) connector ingestion for Alexandria (#3554)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from library.db import LibraryStore, init_library_db
from library.ingest import _insert_passages, _merge_work_topics, _retire_orphan_works
from library.intake import classify_sensitivity
from library.sources import (
    add_source,
    extract_drive_folder_id,
    get_source,
    list_sources,
    matches_source_globs,
    seed_default_source,
)
from library.text_extraction import extract_text_passages, parse_filename

_MAX_FILE_BYTES = 10 * 1024 * 1024
_SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".ipynb"}
_PRIVATE_MARKER_RE = re.compile(r"<<\s*PRIVATE\b", re.IGNORECASE)
_RESTRICTED_CLASSIFICATIONS = frozenset({"private", "sensitive", "vault_sensitive", "restricted", "secret"})
_STAT_KEYS = ("files_seen", "files_indexed", "files_skipped", "files_rejected", "files_removed")
_UPSERT_FILE_SQL = (
    "INSERT INTO files (work_id,source_id,rel_path,source_root,drive_id,drive_link,mtime,size,sha256,status)"
    " VALUES (?,?,?,?,?,?,?,?,?,'present') ON CONFLICT(source_id,rel_path) DO UPDATE SET"
    " work_id=excluded.work_id, source_root=excluded.source_root, drive_id=excluded.drive_id,"
    " drive_link=excluded.drive_link, mtime=excluded.mtime, size=excluded.size,"
    " sha256=excluded.sha256, status='present'"
)
_UPSERT_REJECTION_SQL = (
    "INSERT INTO ingest_rejections (source_root,rel_path,reason,seen_at) VALUES (?,?,?,?)"
    " ON CONFLICT(source_root,rel_path) DO UPDATE SET reason=excluded.reason, seen_at=excluded.seen_at"
)
_FINISH_RUN_SQL = (
    "UPDATE ingest_runs SET finished_at=?, files_seen=?, files_indexed=?, files_skipped=?,"
    " files_rejected=?, files_removed=? WHERE id=?"
)
_default_drive_client: Any = None


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    mime_type: str = "text/markdown"
    mtime: float = 0.0
    size: int | None = None
    web_view_link: str | None = None
    classification: str | None = None
    scope: str | None = None
    content: bytes | str | None = None


def set_default_drive_client(client: Any) -> None:
    global _default_drive_client
    _default_drive_client = client


def get_default_drive_client() -> Any:
    return _default_drive_client


def _normalize_drive_item(item: DriveFile | dict[str, Any]) -> DriveFile:
    if isinstance(item, DriveFile):
        return item
    if not isinstance(item, dict):
        raise ValueError("Drive item must be a DriveFile or mapping")
    fid = str(item.get("file_id") or item.get("id") or "").strip()
    name = str(item.get("name") or item.get("rel_path") or item.get("path") or "").strip()
    if not fid or not name:
        raise ValueError("Drive item requires file_id and name")
    raw_m = item.get("mtime") or item.get("modifiedTime") or 0.0
    mtime = float(raw_m) if isinstance(raw_m, (int, float)) and not isinstance(raw_m, bool) else 0.0
    mime = str(item.get("mime_type") or item.get("mimeType") or "text/markdown")
    sz = int(item["size"]) if item.get("size") is not None else None
    link = item.get("web_view_link") or item.get("webViewLink")
    cls = item.get("classification") or item.get("sensitivity")
    return DriveFile(fid, name, mime, mtime, sz, link, cls, item.get("scope"), item.get("content"))


def _fetch_item_bytes(client: Any, item: DriveFile) -> bytes:
    if item.content is not None:
        return item.content.encode("utf-8") if isinstance(item.content, str) else bytes(item.content)
    for method_name in ("fetch_file", "read_file_content", "download_file"):
        if callable(fn := getattr(client, method_name, None)):
            data = fn(item.file_id)
            return data.encode("utf-8") if isinstance(data, str) else bytes(data)
    raise ValueError(f"Drive client cannot fetch content for file_id={item.file_id}")


def _extract_notebook_passages(raw: bytes) -> list[tuple[str | None, int, int, str]]:
    try:
        nb = json.loads(raw.decode("utf-8", errors="replace"))
        cells = nb.get("cells", []) if isinstance(nb, dict) else []
    except Exception:
        return []
    lines = [
        ("".join(map(str, s)).strip() if isinstance(s := c.get("source"), list) else str(s or "").strip()) + "\n"
        for c in (cells if isinstance(cells, list) else [])
        if isinstance(c, dict) and c.get("source")
    ]
    return extract_text_passages("\n".join(lines).encode("utf-8"), "md") if lines else []


def _classification_rejection_reason(
    item: DriveFile, rel_path: str, raw: bytes, *, source_shared: bool, source_repos: list[str]
) -> str | None:
    if len(raw) == 0 or len(raw) > _MAX_FILE_BYTES:
        return "empty_file" if len(raw) == 0 else "exceeds_max_bytes"
    explicit = (item.classification or "").strip().lower()
    if explicit in _RESTRICTED_CLASSIFICATIONS:
        return f"classification_{explicit}"
    if source_shared and explicit and explicit != "public_reference":
        return f"unshared_classification_{explicit}"
    if item.scope and source_repos and item.scope.strip().lower() not in {r.lower() for r in source_repos}:
        return "scope_mismatch"
    if _PRIVATE_MARKER_RE.search(raw[:262144].decode("utf-8", errors="ignore")):
        return "private_marker"
    req = explicit if explicit in {"public_reference", "confidential_work", "vault_sensitive"} else "public_reference"
    inferred = classify_sensitivity(Path(rel_path).name, raw, requested=req)
    if inferred == "vault_sensitive" or (source_shared and inferred != "public_reference"):
        return f"classification_{inferred}"
    return None


def _index_drive_bytes(
    conn: sqlite3.Connection, *, source_id: str, source_root: str, rel: str, item: DriveFile, raw: bytes
) -> bool:
    ext = Path(rel).suffix.lower() or ".md"
    digest, stem = hashlib.sha256(raw).hexdigest(), Path(rel).stem
    author, title, yr = parse_filename(stem)
    fmt = "ipynb" if ext == ".ipynb" else ("txt" if ext == ".txt" else "md")
    passages = _extract_notebook_passages(raw) if ext == ".ipynb" else extract_text_passages(raw, fmt)
    if not passages:
        return False
    row = conn.execute("SELECT id FROM works WHERE sha256 = ?", (digest,)).fetchone()
    if row:
        work_id = int(row[0])
        conn.execute("UPDATE works SET status='active' WHERE id=?", (work_id,))
    else:
        q_w = "INSERT INTO works (sha256,author,title,year,format,provenance) VALUES (?,?,?,?,?,'google_drive')"
        work_id = int(conn.execute(q_w, (digest, author, title, yr, fmt)).lastrowid or 0)
    link = item.web_view_link or f"https://drive.google.com/file/d/{item.file_id}/view"
    conn.execute(
        _UPSERT_FILE_SQL,
        (work_id, source_id, rel, source_root, item.file_id, link, float(item.mtime), len(raw), digest),
    )
    try:
        _merge_work_topics(conn, work_id, rel)
    except Exception:
        pass
    _insert_passages(conn, work_id, title or stem, passages)
    return True


def ingest_gdrive_source(
    db_path: Path | None = None,
    source_id: str = "default",
    drive_client: Any = None,
    max_files: int | None = None,
    start_after: str | None = None,
) -> dict[str, int | bool | str | None]:
    """Fetch, classify, and index eligible items from a registered gdrive:// source."""
    db = init_library_db(db_path)
    seed_default_source(db)
    source = get_source(db, source_id)
    folder_id, _ = extract_drive_folder_id(str(source.get("drive_folder_id") or source.get("root_path") or ""))
    source_root = f"gdrive://{folder_id}"
    client = drive_client if drive_client is not None else get_default_drive_client()
    if client is None:
        return {k: 0 for k in _STAT_KEYS} | {"scan_complete": False, "next_cursor": start_after}
    list_fn: Callable[[str], Iterable[Any]] | None = (
        getattr(client, "list_files", None) if not callable(client) else client
    )
    if not callable(list_fn):
        raise ValueError("drive_client must provide list_files(folder_id)")
    includes, excludes = list(source.get("include_globs") or []), list(source.get("exclude_globs") or [])
    source_shared, source_repos = bool(source.get("shared")), list(source.get("repos") or [])
    eligible: list[DriveFile] = []
    for it in sorted((_normalize_drive_item(x) for x in list_fn(folder_id)), key=lambda x: x.name.strip("/")):
        rel = it.name.strip("/")
        if not Path(rel).suffix and it.mime_type == "application/vnd.google-apps.document":
            rel = f"{rel}.md"
        if Path(rel).suffix.lower() in _SUPPORTED_SUFFIXES and matches_source_globs(rel, includes, excludes):
            eligible.append(replace(it, name=rel))
    if max_files is not None and max_files > 0:
        after_idx = next((i + 1 for i, it in enumerate(eligible) if it.name == start_after), 0) if start_after else 0
        batch = eligible[after_idx : after_idx + max_files]
        scan_complete = after_idx + len(batch) >= len(eligible)
    else:
        batch, scan_complete = eligible, True
    next_cursor = None if scan_complete or not batch else batch[-1].name
    started = datetime.now(UTC).isoformat()
    stats = {k: 0 for k in _STAT_KEYS}
    seen_paths = {it.name for it in (eligible if scan_complete else batch)}
    with closing(LibraryStore(db).connect()) as conn:
        q_run = "INSERT INTO ingest_runs (started_at,source_root) VALUES (?,?)"
        run_id = conn.execute(q_run, (started, source_root)).lastrowid
        conn.commit()
        for item in batch:
            with conn:
                stats["files_seen"] += 1
                raw = _fetch_item_bytes(client, item)
                reason = _classification_rejection_reason(
                    item, item.name, raw, source_shared=source_shared, source_repos=source_repos
                )
                if reason or not _index_drive_bytes(
                    conn, source_id=source_id, source_root=source_root, rel=item.name, item=item, raw=raw
                ):
                    conn.execute(_UPSERT_REJECTION_SQL, (source_root, item.name, reason or "empty_file", started))
                    stats["files_rejected"] += 1
                    continue
                q_del = "DELETE FROM ingest_rejections WHERE source_root=? AND rel_path=?"
                conn.execute(q_del, (source_root, item.name))
                stats["files_indexed"] += 1
        if scan_complete:
            with conn:
                q_rem = "SELECT rel_path FROM files WHERE source_id=? AND source_root=? AND status='present'"
                for (old_rel,) in conn.execute(q_rem, (source_id, source_root)).fetchall():
                    if old_rel not in seen_paths:
                        q_up = "UPDATE files SET status='removed' WHERE source_id=? AND rel_path=?"
                        conn.execute(q_up, (source_id, old_rel))
                        stats["files_removed"] += 1
                _retire_orphan_works(conn)
        now_s = datetime.now(UTC).isoformat()
        conn.execute(_FINISH_RUN_SQL, (now_s, *stats.values(), run_id))
        q_src = "UPDATE library_sources SET last_ingest_at=?, last_ingest_path=? WHERE id=?"
        conn.execute(q_src, (now_s, next_cursor, source_id))
        conn.commit()
    return {**stats, "scan_complete": scan_complete, "next_cursor": next_cursor}


def ingest_gdrive_uri(
    uri: str,
    *,
    db_path: Path | None = None,
    source_id: str | None = None,
    shared: bool = True,
    drive_client: Any = None,
) -> dict[str, int | bool | str | None]:
    """Ensure a gdrive:// folder URI is registered and ingest its eligible items."""
    db = init_library_db(db_path)
    fid, _ = extract_drive_folder_id(uri)
    hit = next(
        (
            s
            for s in list_sources(db)
            if s.get("drive_folder_id") == fid and s.get("backend_type") == "google_drive_connector"
        ),
        None,
    )
    if hit is None:
        slug = re.sub(r"[^a-z0-9]+", "-", (source_id or f"gdrive-{fid[:16]}").lower()).strip("-") or "gdrive"
        kw: dict[str, Any] = {"backend_type": "google_drive_connector", "drive_folder_id": fid, "shared": shared}
        hit = add_source(db, source_id=slug, label=f"Google Drive ({fid[:12]})", **kw)
    return ingest_gdrive_source(db, source_id=str(hit["id"]), drive_client=drive_client)
