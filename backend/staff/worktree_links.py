"""Fail-closed link inspection for staff cleanup; never descend through links."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.staff.worktree_hygiene")
_GAAI_LINKS = frozenset({".gaai/agents", ".gaai/workflows", ".gaai/contexts"})


def _link_kind(path: Path) -> str:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return "Windows reparse point"
    return "symlink" if path.is_symlink() else ""


def _tracked_gaai_link(path: Path, relative: str) -> bool:
    """Require identical symlink entries in HEAD/index and an unchanged link value."""
    outputs = []
    for args in (("ls-tree", "-z", "HEAD", "--", relative), ("ls-files", "--stage", "-z", "--", relative)):
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(path), *args], capture_output=True, text=True, check=False, timeout=30
        )
        if result.returncode:
            return False
        entries = result.stdout.rstrip("\0").split("\0")
        if len(entries) != 1 or "\t" not in entries[0]:
            return False
        metadata, name = entries[0].split("\t", 1)
        fields = metadata.split()
        if name != relative or len(fields) != 3 or fields[0] != "120000":
            return False
        outputs.append(fields)
    if outputs[0][1] != "blob" or outputs[1][2] != "0" or outputs[0][2] != outputs[1][1]:
        return False
    blob = subprocess.run(  # noqa: S603
        ["git", "-C", str(path), "cat-file", "blob", outputs[0][2]],
        capture_output=True,
        check=False,
        timeout=30,
    )
    return blob.returncode == 0 and blob.stdout == os.fsencode(os.readlink(path / relative))


def link_refusal_reason(path: Path, *, allow_tracked_gaai: bool = False) -> str | None:
    """Pre: inspect ancestors first. Post: only verified GAAI symlinks may pass."""
    for candidate in reversed((path, *path.parents)):
        if kind := _link_kind(candidate):
            return f"{kind} at worktree or ancestor {candidate}"
    allowed = []
    pending = [path]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                candidate = Path(entry.path)
                if kind := _link_kind(candidate):
                    relative = candidate.relative_to(path).as_posix()
                    if kind != "symlink" or not allow_tracked_gaai or relative not in _GAAI_LINKS:
                        return f"unsafe {kind} at {candidate}"
                    allowed.append(relative)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(candidate)
    for relative in allowed:
        if not _tracked_gaai_link(path, relative):
            return f"symlink is not an unchanged tracked GAAI entry: {path / relative}"
    return None


def refuse_worktree_links(path: Path, store: Any = None, run_id: str = "") -> bool:
    """Log every unsafe/uncertain link refusal and record it when a run is known."""
    try:
        reason = link_refusal_reason(path, allow_tracked_gaai=True)
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
