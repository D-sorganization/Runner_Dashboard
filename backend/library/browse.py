"""Node-served local directory browser for library source picker (#2227)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from library.db import library_data_dir

DENIED = {".ssh", ".gemini", ".claude", ".codex", ".git", "$RECYCLE.BIN", "System Volume Information"}


def allowed_browse_roots() -> list[Path]:
    roots = [Path.home().resolve()]
    for d in ("/mnt", "/media", "/Volumes"):
        roots.extend(c.resolve() for c in Path(d).iterdir() if Path(d).is_dir() and c.is_dir())
    if hasattr(os, "listdrives"):
        roots.extend(Path(d).resolve() for d in os.listdrives() if Path(d).is_dir())
    return roots


def denied_dirs() -> set[Path]:
    h = Path.home().resolve()
    cfg = Path(os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR") or h / ".config" / "runner-dashboard").resolve()
    return {library_data_dir().resolve(), cfg, *(h / n for n in (".ssh", ".gemini", ".claude", ".codex"))}


def browse_directory(path: str | Path | None = None, *, limit: int = 500) -> dict[str, Any]:
    roots = allowed_browse_roots() or [Path.home().resolve()]
    target = Path(path).resolve() if path else roots[0]
    if not target.is_dir():
        raise FileNotFoundError(f"directory not found: {target}")
    if not any(target == r or r in target.parents for r in roots):
        raise PermissionError(f"path {target} is outside allowed browse roots")
    if any(target == d or d in target.parents for d in denied_dirs()) or any(
        p.name in DENIED for p in (target, *target.parents)
    ):
        raise PermissionError(f"access to {target} is denied")
    entries: list[dict[str, Any]] = []
    for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir() or item.name in DENIED or item.name.startswith("."):
            continue
        try:
            subs = list(item.iterdir())
            dirs, files = sum(1 for s in subs if s.is_dir()), sum(1 for s in subs if s.is_file())
        except (OSError, PermissionError):
            dirs, files = 0, 0
        entries.append(
            {"name": item.name, "path": str(item.resolve()), "child_dirs": min(dirs, 50), "file_count": min(files, 100)}
        )
        if len(entries) >= limit:
            break
    parent = str(target.parent) if any(target.parent == r or r in target.parent.parents for r in roots) else None
    return {"current_path": str(target), "parent_path": parent, "roots": [str(r) for r in roots], "entries": entries}
