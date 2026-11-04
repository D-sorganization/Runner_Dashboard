"""Worktree bootstrap preflight checks for StaffRunner (#3913).

Extracted from staff.runner_ops to keep file lengths within the 500-line
budget. ``runner_ops`` re-exports these names; keep using
``staff.runner_ops.run_worktree_bootstrap_preflight`` etc. for existing
importers and monkeypatches.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from staff.runner_venv import ensure_staff_pytest_environment

log = logging.getLogger("dashboard.staff.worktree_bootstrap")

_PYTHON_VERSION_SNIPPET = "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
STALE_LOCK_NAMES: tuple[str, ...] = ("index.lock", "shallow.lock", "HEAD.lock", "config.lock")


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
    except (subprocess.TimeoutExpired, Exception):
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


def _git_dir(workspace: Path) -> Path | None:
    """Return the real git directory for ``workspace`` (handles worktrees and submodule checkouts)."""
    dot_git = workspace / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            content = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        prefix = "gitdir:"
        if content.startswith(prefix):
            target = content[len(prefix) :].strip()
            if os.path.isabs(target):
                resolved = Path(target)
            else:
                resolved = (workspace / target).resolve()
            return resolved if resolved.exists() else None
    return None


def is_git_process_active_in_repo(repo_path: Path) -> bool:
    """Check whether any live git process has its cwd or arguments referencing ``repo_path`` (#3935)."""
    try:
        import psutil
    except ImportError:
        return False

    try:
        norm_target = str(repo_path.resolve())
    except Exception:
        norm_target = str(repo_path)

    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "git" not in name:
                continue
            cwd = proc.info.get("cwd") or ""
            if cwd:
                try:
                    resolved_cwd = str(Path(cwd).resolve())
                except Exception:
                    resolved_cwd = cwd
                if resolved_cwd == norm_target or resolved_cwd.startswith(norm_target + os.sep):
                    return True
            cmdline = proc.info.get("cmdline") or []
            for arg in cmdline:
                if norm_target in arg:
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def find_candidate_git_locks(worktree: Path, run: _Run = subprocess.run) -> list[Path]:
    """Find potential stale .lock files in the worktree gitdir, modules, and linked main checkout (#3935)."""
    git_dir = _git_dir(worktree)
    if git_dir is None or not git_dir.is_dir():
        return []

    candidates: set[Path] = set()
    dirs_to_check: list[Path] = [git_dir]

    if (worktree / ".git").is_file():
        main_root = _main_checkout_root(worktree, run)
        if main_root is not None and main_root != worktree:
            main_git = _git_dir(main_root)
            if main_git and (main_git / "modules").is_dir():
                dirs_to_check.append(main_git / "modules")

    if (worktree / ".gitmodules").is_file():
        for sub_git in (*worktree.glob("*/.git"), *worktree.glob("*/*/.git")):
            sub_dir = _git_dir(sub_git.parent)
            if sub_dir and sub_dir.is_dir() and sub_dir not in dirs_to_check:
                dirs_to_check.append(sub_dir)

    for d in dirs_to_check:
        for lock_name in STALE_LOCK_NAMES:
            direct = d / lock_name
            if direct.is_file():
                candidates.add(direct)
            for sublock in d.glob(f"modules/**/{lock_name}"):
                if sublock.is_file():
                    candidates.add(sublock)
            if d.name == "modules":
                for sublock in d.glob(f"**/{lock_name}"):
                    if sublock.is_file():
                        candidates.add(sublock)
    return sorted(candidates)


def clean_stale_git_locks(
    worktree: Path,
    *,
    run: _Run = subprocess.run,
    is_git_process_active: Callable[[Path], bool] | None = None,
) -> list[Path]:
    """Detect and remove stale git/submodule lock files safely (#3935).

    Only removes locks when no live git process is active in the repository or main checkout.
    When a live git process is detected, locks are preserved and logged.
    """
    checker = is_git_process_active or is_git_process_active_in_repo
    locks = find_candidate_git_locks(worktree, run=run)
    if not locks:
        return []

    main_root = _main_checkout_root(worktree, run)
    if checker(worktree) or (main_root is not None and checker(main_root)):
        for lock in locks:
            log.warning("Preserved live git lock (live git process detected): %s", lock)
        return []

    removed: list[Path] = []
    for lock in locks:
        try:
            lock.unlink()
            removed.append(lock)
            log.info("Removed stale git lock: %s", lock)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Failed to remove stale git lock %s: %s", lock, exc)
    return removed


def run_worktree_bootstrap_preflight(
    worktree: Path,
    *,
    role: str = "",
    run: _Run = subprocess.run,
    which: _Which = shutil.which,
    is_git_process_active: Callable[[Path], bool] | None = None,
) -> WorktreeBootstrapPreflight:
    """Bootstrap checks before a staff run starts coding in a worktree.

    Ensures submodules are populated, Python is >=3.11, pytest is available,
    and the repository has a local pre-PR entry point.
    """
    clean_stale_git_locks(worktree, run=run, is_git_process_active=is_git_process_active)

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
