"""Active staff run workdirs from the node-local SQLite store (stdlib-only for #4440)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_ACTIVE_STATUSES = ("queued", "preparing", "running")


def staff_runs_db_path() -> Path:
    configured = os.environ.get("STAFF_RUNS_DB")
    if configured:
        return Path(configured).expanduser()
    config_dir = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    if config_dir:
        base = Path(config_dir).expanduser()
    else:
        base = Path("~/.config/runner-dashboard").expanduser()
    return base / "staff_runs.sqlite3"


def active_staff_workdirs() -> set[Path] | None:
    """Workdirs of queued/preparing/running staff runs, or ``None`` when the store is unreadable."""
    db_path = staff_runs_db_path()
    if not db_path.is_file():
        return set()
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        placeholders = ",".join("?" * len(_ACTIVE_STATUSES))
        rows = conn.execute(
            f"SELECT workdir FROM runs WHERE status IN ({placeholders}) AND workdir != ''",
            _ACTIVE_STATUSES,
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    out: set[Path] = set()
    for (workdir,) in rows:
        if not workdir:
            continue
        try:
            out.add(Path(workdir).resolve())
        except OSError:
            continue
    return out
