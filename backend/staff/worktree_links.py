"""Fail-closed link inspection for staff cleanup; never descend through links."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_real_os = os

log = logging.getLogger("dashboard.staff.worktree_hygiene")


def _platform_os() -> Any:
    import sys

    return getattr(sys.modules[__name__], "os", _real_os)


def _is_windows() -> bool:
    return getattr(_platform_os(), "name", _real_os.name) == "nt"


_O_DIRECTORY = getattr(_real_os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(_real_os, "O_NOFOLLOW", 0)
_OPEN_DIR_FLAGS = _real_os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FILE_ATTRIBUTE_DIRECTORY = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)

# Windows: path-string walks are best-effort. Ancestor checks and in-tree junction
# removal re-verify st_dev/st_ino before delete; without dir_fd on NT, TOCTOU is
# narrowed but not eliminated the way the POSIX fd-relative walk does.


def _link_kind_from_stat(st: os.stat_result) -> str:
    attributes = getattr(st, "st_file_attributes", 0)
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return "Windows reparse point"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return ""


def _link_kind(path: Path) -> str:
    metadata = path.lstat()
    kind = _link_kind_from_stat(metadata)
    if kind:
        return kind
    return "symlink" if path.is_symlink() else ""


def _check_ancestors(path: Path) -> None:
    path = path.absolute()
    for candidate in reversed((path, *path.parents)):
        if kind := _link_kind(candidate):
            raise OSError(f"{kind} at worktree or ancestor {candidate}")


@dataclass(frozen=True)
class _CollectedLink:
    parent_fd: int | None
    parent: Path | None
    name: str
    kind: str
    target: str
    rel_posix: str
    identity: tuple[int, int]


def _stat_entry(link: _CollectedLink) -> os.stat_result:
    if link.parent_fd is not None:
        return _real_os.stat(link.name, dir_fd=link.parent_fd, follow_symlinks=False)
    assert link.parent is not None
    return (link.parent / link.name).lstat()


def _readlink_at(name: str, *, parent_fd: int | None, parent: Path | None, abs_path: Path | None = None) -> str:
    if parent_fd is not None:
        try:
            return _real_os.readlink(name, dir_fd=parent_fd)
        except TypeError:
            if abs_path is not None:
                return _real_os.readlink(abs_path)
    assert parent is not None
    return _real_os.readlink(parent / name)


def _readlink_for_link(link: _CollectedLink, root: Path | None = None) -> str:
    abs_path = (root / link.rel_posix) if root is not None else None
    return _readlink_at(
        link.name,
        parent_fd=link.parent_fd,
        parent=link.parent,
        abs_path=abs_path,
    )


def _collect_links_at_path(directory: Path, rel_prefix: str, links: list[_CollectedLink]) -> None:
    """Path-only tree scan: no retained dir fds (safe for huge .venv / node_modules trees)."""
    _check_ancestors(directory)
    pending: list[tuple[Path, str]] = [(directory, rel_prefix)]
    while pending:
        current, rel = pending.pop()
        _check_ancestors(current)
        with _platform_os().scandir(current) as entries:
            for entry in entries:
                name = entry.name
                candidate = current / name
                identity = entry.stat(follow_symlinks=False)
                child_rel = f"{rel}/{name}" if rel else name
                if kind := _link_kind(candidate):
                    target = _real_os.readlink(candidate)
                    links.append(
                        _CollectedLink(
                            parent_fd=None,
                            parent=current,
                            name=name,
                            kind=kind,
                            target=target,
                            rel_posix=child_rel,
                            identity=(identity.st_dev, identity.st_ino),
                        )
                    )
                elif entry.is_dir(follow_symlinks=False):
                    pending.append((current / name, child_rel))


def _collect_tree_links(path: Path) -> list[_CollectedLink]:
    _check_ancestors(path)
    collected: list[_CollectedLink] = []
    _collect_links_at_path(path, "", collected)
    return collected


def _tree_links(path: Path) -> list[tuple[Path, str, str]]:
    return [(path / link.rel_posix, link.kind, link.target) for link in _collect_tree_links(path)]


def link_refusal_reason(path: Path, *, allow_in_tree: bool = False) -> str | None:
    """Pre: root/ancestors are real directories. Post: inspection never follows links."""
    links = _tree_links(path)
    if links and not allow_in_tree:
        candidate, kind, _target = links[0]
        return f"unsafe {kind} at {candidate}"
    return None


def _verify_link_unchanged(link: _CollectedLink, *, root: Path | None = None) -> None:
    if link.parent is not None:
        candidate = link.parent / link.name
        kind = _link_kind(candidate)
        identity_st = _real_os.stat(candidate, follow_symlinks=False)
        identity = (identity_st.st_dev, identity_st.st_ino)
    else:
        st = _stat_entry(link)
        kind = _link_kind_from_stat(st)
        identity = (st.st_dev, st.st_ino)
    if kind != link.kind or identity != link.identity:
        raise OSError(f"link changed during inspection: {link.rel_posix}")
    if _readlink_for_link(link, root=root) != link.target:
        raise OSError(f"link changed during inspection: {link.rel_posix}")


def _remove_link_entry(link: _CollectedLink, *, root: Path | None = None) -> None:
    _verify_link_unchanged(link, root=root)
    if link.parent_fd is not None:
        st = _stat_entry(link)
        if (
            _is_windows()
            and link.kind == "Windows reparse point"
            and getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_DIRECTORY
        ):
            _real_os.rmdir(link.name, dir_fd=link.parent_fd)
        else:
            _real_os.unlink(link.name, dir_fd=link.parent_fd)
    else:
        assert link.parent is not None
        candidate = link.parent / link.name
        identity_st = _real_os.stat(candidate, follow_symlinks=False)
        if (identity_st.st_dev, identity_st.st_ino) != link.identity:
            raise OSError(f"link changed during inspection: {link.rel_posix}")
        kind = _link_kind(candidate)
        st = candidate.lstat()
        if kind != link.kind:
            raise OSError(f"link changed during inspection: {link.rel_posix}")
        if kind == "Windows reparse point":
            if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_DIRECTORY:
                candidate.rmdir()
            else:
                raise OSError(f"refuse to delete reparse point: {link.rel_posix}")
        else:
            candidate.unlink()
    log.info(
        "unlinked %s %s -> %s (target untouched)",
        link.kind,
        link.rel_posix,
        link.target,
    )


def _unlink_links_at_fd(parent_fd: int, root: Path, rel_prefix: str) -> None:
    """Depth-first unlink: at most one open fd per tree level (no EMFILE on wide trees)."""
    subdirs: list[tuple[str, str]] = []
    with _real_os.scandir(parent_fd) as entries:
        for entry in entries:
            name = entry.name
            st = _real_os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            child_rel = f"{rel_prefix}/{name}" if rel_prefix else name
            if kind := _link_kind_from_stat(st):
                link = _CollectedLink(
                    parent_fd=parent_fd,
                    parent=None,
                    name=name,
                    kind=kind,
                    target=_readlink_at(name, parent_fd=parent_fd, parent=None, abs_path=root / child_rel),
                    rel_posix=child_rel,
                    identity=(st.st_dev, st.st_ino),
                )
                _remove_link_entry(link, root=root)
            elif stat.S_ISDIR(st.st_mode):
                subdirs.append((name, child_rel))
    for name, child_rel in subdirs:
        child_fd = _real_os.open(name, _OPEN_DIR_FLAGS, dir_fd=parent_fd)
        try:
            _unlink_links_at_fd(child_fd, root, child_rel)
        finally:
            _real_os.close(child_fd)


def _unlink_tree_links_unix(path: Path) -> None:
    _check_ancestors(path)
    root_fd = _real_os.open(path, _OPEN_DIR_FLAGS)
    try:
        _unlink_links_at_fd(root_fd, path, "")
    finally:
        _real_os.close(root_fd)


def unlink_worktree_links(path: Path) -> None:
    """Pre: all preservation checks passed. Post: unlink entries only, never targets.

    A directory reparse point on Windows needs rmdir, which removes the junction
    itself. Never recurse into it. Inspection/readlink failures abort removal.
    """
    if _is_windows():
        for link in _collect_tree_links(path):
            _remove_link_entry(link, root=path)
    else:
        _unlink_tree_links_unix(path)
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
