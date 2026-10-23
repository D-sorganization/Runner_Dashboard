"""Context health checks for library pool, lessons, and context pack (#2238)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fleet_events import FleetEvent, get_event_store

Status = Literal["ok", "degraded", "unavailable", "not_installed"]

CACHE_TTL_SECONDS = 60.0
DEFAULT_MAX_INGEST_AGE_SECONDS = 7 * 86400.0
DEFAULT_MAX_PROPOSED_AGE_SECONDS = 14 * 86400.0

_HEALTH_CACHE: dict[str, Any] | None = None
_HEALTH_CACHE_TIME: float = 0.0
_PREVIOUS_STATUS: dict[str, Status] = {}


@dataclass(frozen=True)
class ContextHealthResult:
    name: str
    status: Status
    detail: str
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clear_context_health_cache() -> None:
    global _HEALTH_CACHE, _HEALTH_CACHE_TIME
    _HEALTH_CACHE = None
    _HEALTH_CACHE_TIME = 0.0


def reset_transition_tracker() -> None:
    global _PREVIOUS_STATUS
    _PREVIOUS_STATUS = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def check_library_health(
    db_path: Path | None = None,
    max_ingest_age_seconds: float = DEFAULT_MAX_INGEST_AGE_SECONDS,
) -> ContextHealthResult:
    now = _now_iso()
    if db_path is None:
        try:
            from library.db import library_db_path

            db_path = library_db_path()
        except ImportError:
            return ContextHealthResult("library", "not_installed", "library module not installed", now)

    if not db_path.exists():
        return ContextHealthResult("library", "not_installed", "library database not installed", now)

    try:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_sources'").fetchone()
            if not table:
                return ContextHealthResult("library", "not_installed", "library_sources table not installed", now)

            qc = conn.execute("PRAGMA quick_check").fetchone()
            if not qc or qc[0] != "ok":
                return ContextHealthResult("library", "degraded", f"quick_check failed: {qc[0] if qc else '?'}", now)

            sources = conn.execute("SELECT id, root_path, last_ingest_at, status FROM library_sources").fetchall()
            now_dt = datetime.now(UTC)
            for row in sources:
                sid, status, root, ingest = row["id"], row["status"], row["root_path"], row["last_ingest_at"]
                if status == "unavailable":
                    return ContextHealthResult("library", "degraded", f"source '{sid}' is marked unavailable", now)
                if status == "active":
                    if not Path(root).is_dir():
                        return ContextHealthResult(
                            "library", "degraded", f"source '{sid}' root path unreachable: {root}", now
                        )
                    if ingest:
                        try:
                            if (
                                now_dt - datetime.fromisoformat(str(ingest).replace("Z", "+00:00"))
                            ).total_seconds() > max_ingest_age_seconds:
                                return ContextHealthResult(
                                    "library", "degraded", f"source '{sid}' ingest is stale (> 7d)", now
                                )
                        except (ValueError, TypeError):
                            pass
            return ContextHealthResult("library", "ok", f"{len(sources)} source(s) healthy", now)
    except sqlite3.DatabaseError as exc:
        return ContextHealthResult("library", "unavailable", f"database error: {exc}", now)
    except Exception as exc:
        return ContextHealthResult("library", "unavailable", f"check error: {exc}", now)


def check_pool_health() -> ContextHealthResult:
    now = _now_iso()
    try:
        import library.pool  # type: ignore[import-not-found]  # noqa: F401

        return ContextHealthResult("pool", "ok", "pool nodes online", now)
    except ImportError:
        return ContextHealthResult("pool", "not_installed", "library pool not installed", now)


def check_memory_health(
    db_path: Path | None = None,
    max_proposed_age_seconds: float = DEFAULT_MAX_PROPOSED_AGE_SECONDS,
) -> ContextHealthResult:
    now = _now_iso()
    if db_path is None:
        try:
            from memory.store import default_db_path

            db_path = default_db_path()
        except ImportError:
            return ContextHealthResult("memory", "not_installed", "memory module not installed", now)

    if not db_path.exists():
        return ContextHealthResult("memory", "not_installed", "memory database not installed", now)

    try:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_entries'").fetchone()
            if not table:
                return ContextHealthResult("memory", "not_installed", "memory_entries table not installed", now)

            row = conn.execute(
                "SELECT COUNT(*) FROM memory_entries "
                "WHERE entry_type='lesson' AND expires_at IS NOT NULL AND expires_at <= ? AND superseded_by IS NULL",
                (datetime.now(UTC).isoformat(),),
            ).fetchone()
            if row and row[0] > 0:
                return ContextHealthResult(
                    "memory", "degraded", f"{row[0]} expired lesson(s) still active (must be 0)", now
                )

            row = conn.execute(
                "SELECT COUNT(*) FROM memory_entries "
                "WHERE entry_type='lesson' AND (tags_json LIKE '%quarantine%' OR tags_json LIKE '%quarantined%')"
            ).fetchone()
            if row and row[0] > 0:
                return ContextHealthResult("memory", "degraded", f"{row[0]} quarantined lesson(s) present", now)

            row = conn.execute(
                "SELECT MIN(created_at) FROM memory_entries "
                "WHERE entry_type='lesson' AND tags_json LIKE '%proposed%' AND superseded_by IS NULL"
            ).fetchone()
            if row and row[0]:
                try:
                    if (
                        datetime.now(UTC) - datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                    ).total_seconds() > max_proposed_age_seconds:
                        return ContextHealthResult(
                            "memory", "degraded", "proposed lesson backlog age exceeds threshold (> 14d)", now
                        )
                except (ValueError, TypeError):
                    pass
            return ContextHealthResult("memory", "ok", "assistant memory healthy", now)
    except sqlite3.DatabaseError as exc:
        return ContextHealthResult("memory", "unavailable", f"database error: {exc}", now)
    except Exception as exc:
        return ContextHealthResult("memory", "unavailable", f"check error: {exc}", now)


def check_context_pack_health() -> ContextHealthResult:
    now = _now_iso()
    try:
        from staff.standards_registry import load_fleet_standards

        standards = load_fleet_standards()
        if standards.source_path == "<builtin>":
            return ContextHealthResult(
                "context_pack", "degraded", "standards using <builtin> fallback (FLEET.md not resolved)", now
            )
        if not standards.digest:
            return ContextHealthResult("context_pack", "degraded", "standards digest missing", now)
        return ContextHealthResult(
            "context_pack",
            "ok",
            f"standards {standards.name}@{standards.version} pinned ({standards.digest[:12]})",
            now,
        )
    except ImportError:
        return ContextHealthResult("context_pack", "not_installed", "standards registry not installed", now)
    except Exception as exc:
        return ContextHealthResult("context_pack", "degraded", f"standards check error: {exc}", now)


def check_all_context_health(
    library_db: Path | None = None,
    memory_db: Path | None = None,
    *,
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    global _HEALTH_CACHE, _HEALTH_CACHE_TIME

    now_t = time.time()
    if not force and _HEALTH_CACHE is not None and (now_t - _HEALTH_CACHE_TIME < CACHE_TTL_SECONDS):
        return _HEALTH_CACHE

    results: dict[str, ContextHealthResult] = {
        "library": check_library_health(library_db),
        "pool": check_pool_health(),
        "memory": check_memory_health(memory_db),
        "context_pack": check_context_pack_health(),
    }

    for name, res in results.items():
        prev = _PREVIOUS_STATUS.get(name, "ok")
        curr = res.status
        if curr in ("degraded", "unavailable") and prev not in ("degraded", "unavailable"):
            try:
                ev = FleetEvent(
                    ts=int(now_t * 1000),
                    severity="critical" if curr == "unavailable" else "warning",
                    kind="context_health",
                    title=f"Context health {name}: {curr}",
                    detail=res.detail,
                )
                get_event_store().record(ev)
            except Exception:
                pass
        _PREVIOUS_STATUS[name] = curr

    serialized = {k: v.to_dict() for k, v in results.items()}
    _HEALTH_CACHE = serialized
    _HEALTH_CACHE_TIME = now_t
    return serialized
