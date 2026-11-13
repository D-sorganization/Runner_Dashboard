"""Virtual environment management for staff run worktrees (#3902).

Under systemd ProtectHome=read-only or sandboxed execution environments,
user directories such as ~/.local and ~/.cache are read-only. Pip cannot
fall back to --user installs. This module provides writable virtual environment
creation and reuse (per-worktree or per-role) so test dependencies (e.g. pytest)
are installed cleanly into dedicated environments.
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from staff import workspace

log = logging.getLogger("dashboard.staff.runner_venv")

_Run = Callable[..., subprocess.CompletedProcess[str]]


def _is_writable_dir(path: Path) -> bool:
    """Check whether a directory can be created and written to."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".probe_write_{uuid.uuid4().hex[:8]}"
        probe.touch(exist_ok=False)
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def resolve_staff_venv_dir(worktree: Path, *, role: str = "") -> Path:
    """Resolve a writable location for a staff run virtual environment (#3902).

    Checks in order:
    1. STAFF_VENV_DIR environment override (if set and writable).
    2. $XDG_CACHE_HOME/runner-dashboard/venvs/<role> (if set and writable).
    3. staff_worktrees_root()/.venvs/<role> (if role provided and root writable).
    4. Per-worktree venv: worktree/.venv (run directory fallback).
    """
    custom = os.environ.get("STAFF_VENV_DIR", "").strip()
    if custom:
        target = Path(custom) / role if role else Path(custom)
        if _is_writable_dir(target):
            return target

    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        target = Path(xdg_cache) / "runner-dashboard" / "venvs" / (role or "default")
        if _is_writable_dir(target):
            return target

    if role:
        try:
            wt_root = workspace.staff_worktrees_root()
            target = wt_root / ".venvs" / role
            if _is_writable_dir(target):
                return target
        except Exception:  # noqa: BLE001
            pass

    return worktree / ".venv"


def venv_python_executable(venv_dir: Path) -> Path:
    """Locate the Python executable inside a virtual environment."""
    if os.name == "nt":
        exe = venv_dir / "Scripts" / "python.exe"
        return exe if exe.exists() else venv_dir / "Scripts" / "python"
    exe = venv_dir / "bin" / "python"
    return exe if exe.exists() else venv_dir / "bin" / "python3"


def ensure_staff_pytest_environment(
    worktree: Path,
    base_python: str,
    *,
    role: str = "",
    run: _Run = subprocess.run,
) -> tuple[bool, str, str, str]:
    """Ensure pytest is available in base_python or a writable venv (#3902).

    Returns:
        (ok, python_executable, preflight_attention_message, venv_dir_str)
    """
    venv_dir = resolve_staff_venv_dir(worktree, role=role)
    venv_python = venv_python_executable(venv_dir)

    # 1. If venv exists, check if pytest is already working inside it (reused venv).
    if venv_python.is_file():
        check = run(
            [str(venv_python), "-m", "pytest", "--version"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if check.returncode == 0:
            return True, str(venv_python), "", str(venv_dir)

    # 2. Check if base_python already has pytest installed (e.g. CI environments).
    base_check = run(
        [base_python, "-m", "pytest", "--version"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if base_check.returncode == 0:
        return True, base_python, "", ""

    # 3. Neither has pytest: create writable venv if needed and install pytest. Never pip --user.
    if not venv_python.is_file():
        create = run(
            [base_python, "-m", "venv", str(venv_dir)],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if create.returncode != 0:
            detail = (create.stderr or create.stdout or "venv creation failed").strip()
            return (
                False,
                base_python,
                f"failed to create virtual environment in {venv_dir}: {detail}",
                str(venv_dir),
            )
        venv_python = venv_python_executable(venv_dir)

    target_python = str(venv_python)
    install = run(
        [target_python, "-m", "pip", "install", "pytest"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if install.returncode != 0:
        detail = (install.stderr or install.stdout or "pip install failed").strip()
        return (
            False,
            target_python,
            f"pytest is unavailable and could not be installed with {target_python}: {detail}",
            str(venv_dir),
        )

    verify = run(
        [target_python, "-m", "pytest", "--version"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if verify.returncode != 0:
        detail = (verify.stderr or verify.stdout or "pytest still unavailable").strip()
        return (
            False,
            target_python,
            f"pytest remains unavailable for {target_python} after install attempt: {detail}",
            str(venv_dir),
        )

    return True, target_python, "", str(venv_dir)
