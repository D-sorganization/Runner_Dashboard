"""Versioned release directory layout and atomic live switch."""

from __future__ import annotations

from pathlib import Path


def release_root(base_dir: Path) -> Path:
    return base_dir.parent / "dashboard-releases"


def release_dir(base_dir: Path, commit_sha: str) -> Path:
    return release_root(base_dir) / commit_sha.lower()


def atomic_live_switch(live_dir: Path, new_target: Path) -> None:
    """Point ``live_dir`` at ``new_target`` via symlink (atomic on same volume).

    Pre: ``new_target`` exists and is a directory.
    Post: ``live_dir`` is a symlink to ``new_target``.
    """
    if not new_target.is_dir():
        raise FileNotFoundError(new_target)
    live_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = live_dir.with_name(f"{live_dir.name}.switch.tmp")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(new_target, target_is_directory=True)
    if live_dir.is_symlink():
        live_dir.unlink()
    elif live_dir.exists():
        raise IsADirectoryError(f"{live_dir} is not a symlink; run the one-time migrate step before auto-deploy")
    tmp.rename(live_dir)
