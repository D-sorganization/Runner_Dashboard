"""Quarantine local dirt in STAFF_RM_ROOT so rm_sync can fast-forward safely (#2976)."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.rm_sync")

_QUARANTINE_DIRNAME = "rm_dirty_quarantine"


def _parse_porcelain_line(line: str) -> tuple[str, str] | None:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    if line.startswith("?? "):
        return "??", line[3:]
    if len(line) >= 3 and line[2] == " ":
        payload = line[3:]
        status = line[:2]
    elif len(line) >= 2 and line[1] == " ":
        payload = line[2:]
        status = f"{line[0]} "
    else:
        return None
    if " -> " in payload:
        _, path = payload.split(" -> ", 1)
    else:
        path = payload.strip('"')
    return status, path


def quarantine_dirty_worktree(
    root: Path,
    *,
    git_run: Any,
    quarantine_parent: Path | None = None,
) -> dict[str, Any] | None:
    """Copy dirty paths aside, restore a clean tree, and log; never silently discard work."""
    porcelain = git_run(root, "status", "--porcelain", "--untracked-files=all")
    if not porcelain:
        return None
    entries: list[dict[str, str]] = []
    restore_paths: list[str] = []
    clean_paths: list[str] = []
    for line in porcelain.splitlines():
        parsed = _parse_porcelain_line(line)
        if parsed is None:
            continue
        status, path = parsed
        entries.append({"status": status, "path": path})
        if status == "??":
            clean_paths.append(path)
        else:
            restore_paths.append(path)
    if not entries:
        return None

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S-%fZ")
    head = git_run(root, "rev-parse", "--short", "HEAD")
    dest = (quarantine_parent or (_config_dir() / _QUARANTINE_DIRNAME)) / f"{stamp}-{head}"
    dest.mkdir(parents=True, exist_ok=False)

    for entry in entries:
        rel = Path(entry["path"])
        src = root / rel
        target = dest / rel
        if src.exists() or src.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir() and not src.is_symlink():
                shutil.copytree(src, target, symlinks=True)
            else:
                shutil.copy2(src, target, follow_symlinks=False)

    manifest = {
        "root": str(root),
        "head": head,
        "quarantined_at": stamp,
        "entries": entries,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if restore_paths:
        git_run(root, "restore", "--source=HEAD", "--worktree", "--", *restore_paths)
    if clean_paths:
        git_run(root, "clean", "-fd", "--", *clean_paths)

    paths = [entry["path"] for entry in entries]
    summary: dict[str, Any] = {
        "dir": str(dest),
        "count": len(entries),
        "paths": paths,
    }
    log.warning(
        "quarantined %d local change(s) in STAFF_RM_ROOT to %s: %s",
        summary["count"],
        dest,
        ", ".join(paths),
    )
    return summary
