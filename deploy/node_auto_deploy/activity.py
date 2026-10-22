"""Fail-closed local staff-run and chat activity probe for release quiescence."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class ActivityUnavailableError(RuntimeError):
    """Activity state could not be established safely."""


def staff_database_path(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    configured = env.get("STAFF_RUNS_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    config_dir = env.get("RUNNER_DASHBOARD_CONFIG_DIR", "").strip()
    root = Path(config_dir).expanduser() if config_dir else Path.home() / ".config" / "runner-dashboard"
    return root / "staff_runs.sqlite3"


def local_activity_count(database: Path) -> int:
    """Count active staff runs and non-user pending/streaming chat replies.

    The database must already exist and contain both known tables/columns. An
    unknown or unreadable schema is not interpreted as an idle dashboard.
    """
    try:
        path = database.expanduser().resolve(strict=True)
        if not path.is_file():
            raise ActivityUnavailableError("staff activity database is not a regular file")
        uri = f"{path.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"runs", "messages"}.issubset(tables):
                raise ActivityUnavailableError("staff activity database schema is unavailable")
            run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
            if not {"status"}.issubset(run_columns) or not {"delivery", "author_kind"}.issubset(message_columns):
                raise ActivityUnavailableError("staff activity database schema is incomplete")
            active_runs = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'preparing', 'running')"
            ).fetchone()[0]
            pending_replies = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE author_kind != 'user' AND delivery IN ('pending', 'streaming')"
            ).fetchone()[0]
            return int(active_runs) + int(pending_replies)
    except ActivityUnavailableError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ActivityUnavailableError(f"could not read staff activity state: {exc}") from exc
