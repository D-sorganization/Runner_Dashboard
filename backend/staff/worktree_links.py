"""Fail-closed link inspection for staff cleanup; never descend through links."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.worktree_hygiene")


def _link_kind(path: Path) -> str:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return "Windows reparse point"
    return "symlink" if stat.S_ISLNK(metadata.st_mode) or path.is_symlink() else ""


def _check_ancestors(path: Path) -> None:
    path = path.absolute()
    for candidate in reversed((path, *path.parents)):
        if kind := _link_kind(candidate):
            raise OSError(f"{kind} at worktree or ancestor {candidate}")


def _tree_links(path: Path) -> list[tuple[Path, str, str]]:
    """Inspect without following links; finish inspection before any mutation."""
    _check_ancestors(path)
    links = []
    pending = [path]
    while pending:
        directory = pending.pop()
        _check_ancestors(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                candidate = Path(entry.path)
                if kind := _link_kind(candidate):
                    links.append((candidate, kind, os.readlink(candidate)))
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(candidate)
    return links


def link_refusal_reason(path: Path, *, allow_in_tree: bool = False) -> str | None:
    """Pre: root/ancestors are real directories. Post: inspection never follows links."""
    links = _tree_links(path)
    if links and not allow_in_tree:
        candidate, kind, _target = links[0]
        return f"unsafe {kind} at {candidate}"
    return None


def unlink_worktree_links(path: Path) -> None:
    """Pre: all preservation checks passed. Post: unlink entries only, never targets.

    A directory reparse point on Windows needs rmdir, which removes the junction
    itself. Never recurse into it. Inspection/readlink failures abort removal.
    """
    for candidate, kind, target in _tree_links(path):
        _check_ancestors(candidate.parent)
        if _link_kind(candidate) != kind or os.readlink(candidate) != target:
            raise OSError(f"link changed during inspection: {candidate}")
        if (
            os.name == "nt"
            and kind == "Windows reparse point"
            and getattr(candidate.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_DIRECTORY
        ):
            candidate.rmdir()
        else:
            candidate.unlink()
        log.info("unlinked %s %s -> %s (target untouched)", kind, candidate.relative_to(path).as_posix(), target)
    if reason := link_refusal_reason(path):
        raise OSError(reason)


def refuse_worktree_links(path: Path, store: Any = None, run_id: str = "") -> bool:
    """Log every unsafe/uncertain link refusal and record it when a run is known."""
    try:
        reason = link_refusal_reason(path, allow_in_tree=True)
    except (OSError, subprocess.SubprocessError) as exc:
        reason = f"link inspection failed: {exc}"
    if reason is None:
        return False
    message = f"worktree preserved at {path}: {reason}"
    log.warning(message)
    if store is not None and run_id:
        try:
            if store.get_run(run_id) is not None:
                store.append_event(run_id, "worktree", message)
        except Exception as exc:  # noqa: BLE001 — logging remains available if the store is down
            log.warning("worktree refusal event unavailable for %s: %s", path, exc)
    return True
