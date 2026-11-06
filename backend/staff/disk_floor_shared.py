"""Runner-schedule disk floor probe shared with the staff hub.

Mirrors ``disk_floor_breach`` / ``disk_free_gb`` in ``deploy/runner-scheduler.py`` so
staff worktree creation honors the same optional ``resource_floor`` thresholds as the
runner scheduler. Keep the latch and breach logic aligned when changing either copy.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.disk_floor")

DISK_FLOOR_LATCH_PATH = Path(os.environ.get("RUNNER_DISK_FLOOR_LATCH", "/var/lib/runner-scheduler/disk-floor.json"))
DEFAULT_RUNNER_SCHEDULE_CONFIG = Path(
    os.environ.get(
        "RUNNER_SCHEDULE_CONFIG",
        str(Path.home() / ".config" / "runner-dashboard" / "runner-schedule.json"),
    )
).expanduser()


def disk_free_gb(path: str) -> float | None:
    """Free space at ``path`` in GB, or None when it cannot be read (scheduler-compatible)."""
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return None


def disk_floor_breach(floor: dict[str, Any], *, latch_path: Path | None = None) -> str | None:
    """Disk-floor breach with optional readmit hysteresis (matches runner-scheduler)."""
    latch = latch_path if latch_path is not None else DISK_FLOOR_LATCH_PATH
    disk_path = str(floor.get("disk_path", "/"))
    free = disk_free_gb(disk_path)
    if free is None:
        return None
    minimum = float(floor["min_disk_free_gb"])
    readmit = floor.get("readmit_disk_free_gb")
    readmit_f = float(readmit) if readmit is not None else None
    if free < minimum:
        if readmit_f is not None and not latch.exists():
            try:
                latch.parent.mkdir(parents=True, exist_ok=True)
                latch.write_text(
                    json.dumps({"since": datetime.now(UTC).isoformat(), "free_gb": round(free, 1)}) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                log.warning("disk-floor latch not written (%s): %s", latch, exc)
        return f"disk_free={free:.1f}GB<{minimum}"
    if readmit_f is not None and latch.exists():
        if free < readmit_f:
            return f"disk_free={free:.1f}GB<{readmit_f} (readmit)"
        latch.unlink(missing_ok=True)
    return None


def staff_disk_floor_breach_reason(*, config_path: Path | None = None, latch_path: Path | None = None) -> str | None:
    """Return a breach reason when schedule ``resource_floor`` blocks staff disk use, else None.

    When the schedule file is missing or has no ``resource_floor``, returns None.
    An unreadable disk probe returns None (same as scheduler: do not treat as breach).
    """
    path = config_path if config_path is not None else DEFAULT_RUNNER_SCHEDULE_CONFIG
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    floor = payload.get("resource_floor")
    if not isinstance(floor, dict) or "min_disk_free_gb" not in floor:
        return None
    return disk_floor_breach(floor, latch_path=latch_path)
