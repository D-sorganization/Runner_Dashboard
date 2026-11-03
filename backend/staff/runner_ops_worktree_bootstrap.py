"""Worktree bootstrap preflight checks for StaffRunner (#3913).

Extracted from staff.runner_ops to keep file lengths within the 500-line
budget. ``runner_ops`` re-exports these names; keep using
``staff.runner_ops.run_worktree_bootstrap_preflight`` etc. for existing
importers and monkeypatches.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from staff.runner_venv import ensure_staff_pytest_environment
from staff.worktree_git_lock_cleanup import clear_stale_git_locks

_PYTHON_VERSION_SNIPPET = "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"


@dataclass(frozen=True)
class WorktreeBootstrapPreflight:
    ok: bool
    attention: str = ""
    python_executable: str = ""
    pre_pr_entrypoint: str = ""
    venv_dir: str = ""
    failure_class: str = ""
    retryable: bool = False
    remediation: str = ""


def resolve_repo_pre_pr_entrypoint(worktree: Path, *, python_executable: str = "python3") -> str | None:
    """Repo-local pre-PR check command for prompts and diagnostics (#2796).

    Preference order is specific -> generic. ``scripts.pre_pr`` is RM-only, so
    other repositories need their own local entry point.
    """
    entries = (
        ("scripts/pre_pr.py", f"{python_executable} -m scripts.pre_pr"),
        ("shared_scripts/pre_pr.py", f"{python_executable} shared_scripts/pre_pr.py"),
        ("scripts/verify_merge_gates.py", f"{python_executable} scripts/verify_merge_gates.py --repository-root ."),
        ("scripts/agent-check", f"{python_executable} scripts/agent-check"),
    )
    for rel, command in entries:
        if (worktree / rel).is_file():
            return command
    return None


_Run = Callable[..., subprocess.CompletedProcess[str]]
_Which = Callable[[str], str | None]


def _run_checked(
    run: _Run,
    worktree: Path,
    command: list[str],
    *,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return run(
        command,
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _python_version(worktree: Path, python_executable: str, run: _Run) -> tuple[int, int] | None:
    proc = _run_checked(run, worktree, [python_executable, "-c", _PYTHON_VERSION_SNIPPET], timeout=30)
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    parts = raw.split(".", 1)
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def _select_python311_or_newer(worktree: Path, run: _Run, which: _Which) -> str | None:
    seen: set[str] = set()
    for candidate in ("python3.11", "python3", "python"):
        executable = (which(candidate) or "").strip()
        if not executable or executable in seen:
            continue
        seen.add(executable)
        version = _python_version(worktree, executable, run)
        if version is not None and version >= (3, 11):
            return executable
    return None


_SUBMODULE_ATTEMPT_TIMEOUT = 300
_SUBMODULE_ATTEMPTS = 2  # one try plus one retry


def _submodules_already_populated(worktree: Path, run: _Run) -> bool:
    """True when ``git submodule status`` shows no pending init/update ('-'/'+'/'U' prefix)."""
    try:
        status = _run_checked(run, worktree, ["git", "submodule", "status"], timeout=30)
    except subprocess.TimeoutExpired:
        return False
    if status.returncode != 0:
        return False
    lines = [line for line in (status.stdout or "").splitlines() if line.strip()]
    return bool(lines) and not any(line[:1] in ("-", "+", "U") for line in lines)


def _main_checkout_root(worktree: Path, run: _Run) -> Path | None:
    """Resolve the primary checkout a linked worktree was branched from, if any."""
    try:
        listing = _run_checked(run, worktree, ["git", "worktree", "list", "--porcelain"], timeout=30)
    except subprocess.TimeoutExpired:
        return None
    if listing.returncode != 0:
        return None
    for line in (listing.stdout or "").splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree ") :].strip())
    return None


def _git_version(worktree: Path, run: _Run) -> tuple[int, ...] | None:
    """Probe the git version tuple, e.g. (2, 34, 1) or (2, 47, 1) (#3921)."""
    try:
        proc = _run_checked(run, worktree, ["git", "--version"], timeout=10)
    except (subprocess.TimeoutExpired, Exception):
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    for token in raw.split():
        nums = [int(p) for p in token.split(".") if p.isdigit()]
        if len(nums) >= 2:
            return tuple(nums)
    return None


def _submodule_update_command(worktree: Path, run: _Run) -> list[str]:
    """Shallow, filtered submodule fetch, reusing the main checkout's objects when present (#3913, #3921)."""
    command = [
        "git",
        "-c",
        "protocol.version=2",
        "submodule",
        "update",
        "--init",
        "--recursive",
        "--depth",
        "1",
        "--jobs",
        "4",
    ]
    git_ver = _git_version(worktree, run)
    if git_ver is not None and git_ver[:2] >= (2, 36):
        command.append("--filter=blob:none")

    main_root = _main_checkout_root(worktree, run)
    if main_root is not None and main_root != worktree and (main_root / ".git" / "modules").is_dir():
        command.extend(["--reference", str(main_root), "--dissociate"])
    return command


def _bootstrap_submodules(worktree: Path, run: _Run) -> WorktreeBootstrapPreflight | None:
    """Run the submodule update with a bounded per-attempt timeout and one retry.

    Returns a failing ``WorktreeBootstrapPreflight`` on timeout/error, or ``None`` on success.
    """
    command = _submodule_update_command(worktree, run)
    last_timed_out = False
    for _attempt in range(_SUBMODULE_ATTEMPTS):
        try:
            submodule = _run_checked(run, worktree, command, timeout=_SUBMODULE_ATTEMPT_TIMEOUT)
        except subprocess.TimeoutExpired:
            last_timed_out = True
            continue
        if submodule.returncode != 0:
            detail = (submodule.stderr or submodule.stdout or "unknown git submodule error").strip()
            return WorktreeBootstrapPreflight(ok=False, attention=f"submodule init failed: {detail}")
        return None
    if last_timed_out:
        return WorktreeBootstrapPreflight(
            ok=False,
            attention=(
                f"git submodule update timed out after {_SUBMODULE_ATTEMPT_TIMEOUT}s on "
                f"{_SUBMODULE_ATTEMPTS} attempt(s)"
            ),
            failure_class="submodule_timeout",
            retryable=True,
            remediation=(
                "Submodule fetch exceeded the per-attempt timeout; re-dispatch the run. "
                "A warm main checkout (populated .git/modules) speeds up subsequent attempts "
                "via --reference-if-able."
            ),
        )
    return None


def run_worktree_bootstrap_preflight(
    worktree: Path,
    *,
    role: str = "",
    run: _Run = subprocess.run,
    which: _Which = shutil.which,
    live_git_common_dirs: set[Path] | None = None,
) -> WorktreeBootstrapPreflight:
    """Bootstrap checks before a staff run starts coding in a worktree.

    Ensures submodules are populated, Python is >=3.11, pytest is available,
    and the repository has a local pre-PR entry point.
    """
    clear_stale_git_locks(worktree, run=run, live_common_dirs=live_git_common_dirs)
    if (worktree / ".gitmodules").is_file() and not _submodules_already_populated(worktree, run):
        failure = _bootstrap_submodules(worktree, run)
        if failure is not None:
            return failure

    python_executable = _select_python311_or_newer(worktree, run, which)
    if not python_executable:
        return WorktreeBootstrapPreflight(
            ok=False,
            attention="python 3.11+ is required in the worktree environment, but no compatible interpreter was found",
        )

    ok, target_python, attention, venv_dir = ensure_staff_pytest_environment(
        worktree, python_executable, role=role, run=run
    )
    if not ok:
        return WorktreeBootstrapPreflight(
            ok=False, attention=attention, python_executable=target_python, venv_dir=venv_dir
        )

    pre_pr = resolve_repo_pre_pr_entrypoint(worktree, python_executable=target_python)
    if not pre_pr:
        return WorktreeBootstrapPreflight(
            ok=False,
            attention=(
                "no repository pre-PR entry point found (checked scripts/pre_pr.py, "
                "shared_scripts/pre_pr.py, scripts/verify_merge_gates.py and scripts/agent-check)"
            ),
            python_executable=target_python,
            venv_dir=venv_dir,
        )

    return WorktreeBootstrapPreflight(
        ok=True, python_executable=target_python, pre_pr_entrypoint=pre_pr, venv_dir=venv_dir
    )
