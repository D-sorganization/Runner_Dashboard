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


def _parse_porcelain_z(output: str) -> list[dict[str, str]]:
    """Entries from ``git status --porcelain -z``: unquoted paths, renames as ``new NUL orig``.

    ``-z`` keeps paths verbatim (plain porcelain C-quotes spaces and non-ASCII, which then
    match nothing on disk). A caller that strips stdout may drop the first record's leading
    space (`` M path`` -> ``M path``); that record is read as an unstaged change.
    """
    records = output.split("\0")
    entries: list[dict[str, str]] = []
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if len(rec) < 3:
            continue
        if rec[2] == " ":
            status, path = rec[:2], rec[3:]
        elif rec[1] == " ":
            status, path = f" {rec[0]}", rec[2:]
        else:
            continue
        entry = {"status": status, "path": path}
        if status[0] in "RC" and i < len(records):
            entry["orig_path"] = records[i]
            i += 1
        entries.append(entry)
    return entries


def quarantine_dirty_worktree(
    root: Path,
    *,
    git_run: Any,
    quarantine_parent: Path | None = None,
) -> dict[str, Any] | None:
    """Copy dirty paths aside, restore a clean tree, and log; never silently discard work.

    Post: every reported path is copied under the returned ``dir`` (staged content also as
    ``staged.patch``), and the index and worktree match ``HEAD`` for those paths.
    """
    porcelain = git_run(root, "status", "--porcelain", "-z", "--untracked-files=all")
    if not porcelain:
        return None
    entries = _parse_porcelain_z(porcelain)
    if not entries:
        return None
    clean_paths = [e["path"] for e in entries if e["status"] == "??"]
    restore_paths = [p for e in entries if e["status"] != "??" for p in (e["path"], e.get("orig_path")) if p]
    staged = any(e["status"][0] not in " ?" for e in entries)

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

    if staged:
        # The index can differ from the worktree copy saved above; keep it as a patch.
        patch = git_run(root, "diff", "--cached", "--binary")
        (dest / "staged.patch").write_text(patch + "\n", encoding="utf-8")
    if restore_paths:
        git_run(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *restore_paths)
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
