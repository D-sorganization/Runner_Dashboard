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
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from staff.runner_venv import ensure_staff_pytest_environment

log = logging.getLogger("dashboard.staff.worktree_bootstrap")

_PYTHON_VERSION_SNIPPET = "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
STALE_LOCK_NAMES: tuple[str, ...] = ("index.lock", "shallow.lock", "HEAD.lock", "config.lock")
_SUBMODULE_INIT_LOCK_BASENAME = "staff-submodule-init.lock"
STALE_GIT_LOCK_MAX_AGE_S = float(os.environ.get("STAFF_STALE_GIT_LOCK_AGE_SECONDS", "300"))


@dataclass(frozen=True)
class WorktreeBootstrapPreflight:
    ok: bool
    attention: str = ""
    python_executable: str = ""
    pre_pr_entrypoint: str = ""
    pre_pr_advisory: str = ""
    venv_dir: str = ""
    failure_class: str = ""
    retryable: bool = False
    remediation: str = ""


def role_requires_pre_pr_entrypoint(role: str) -> bool:
    """Whether bootstrap must find a runnable local pre-PR script (#4331).

    Roles that do not open pull requests (``permissions.open_pr``) and explicit
    ``code_read_only`` roles skip the gate — e.g. ``code-reviewer`` on private
    repos such as ``work_private`` that ship no fleet pre-PR scripts. Unknown
    roles fail closed.
    """
    if not role:
        return True
    from staff.roles import load_roles

    spec = load_roles().get(role)
    if spec is None:
        return True
    if spec.code_read_only:
        return False
    if not spec.opens_pr:
        return False
    return True


def _pre_pr_candidate_commands(worktree: Path, python_executable: str) -> list[tuple[str, list[str]]]:
    """Ordered pre-PR probe commands for a worktree (specific before generic)."""
    py = python_executable
    candidates: list[tuple[str, list[str]]] = []
    if (worktree / "scripts" / "pre_pr.py").is_file():
        candidates.append(("scripts/pre_pr.py", [py, "-m", "scripts.pre_pr"]))
        candidates.append(("scripts/pre_pr.py", [py, "scripts/pre_pr.py"]))
    if (worktree / "shared_scripts" / "pre_pr.py").is_file():
        candidates.append(("shared_scripts/pre_pr.py", [py, "shared_scripts/pre_pr.py"]))
    if (worktree / "scripts" / "verify_merge_gates.py").is_file():
        candidates.append(
            (
                "scripts/verify_merge_gates.py",
                [py, "scripts/verify_merge_gates.py", "--repository-root", "."],
            )
        )
    agent_check = worktree / "scripts" / "agent-check"
    if agent_check.is_file():
        candidates.append(("scripts/agent-check", [py, "scripts/agent-check"]))
    return candidates


def _pre_pr_probe_succeeded(
    worktree: Path,
    argv: list[str],
    *,
    run: _Run,
    rel: str,
) -> bool:
    """True when the entry point is invokable (``--help``) without running merge gates."""
    if rel == "scripts/agent-check":
        return True
    probe = [*argv, "--help"]
    try:
        proc = _run_checked(run, worktree, probe, timeout=60)
    except subprocess.TimeoutExpired:
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().lower()
        if "no module named" in detail or "modulenotfounderror" in detail:
            return False
    return proc.returncode == 0


def resolve_repo_pre_pr_entrypoint(
    worktree: Path,
    *,
    python_executable: str = "python3",
    run: _Run = subprocess.run,
) -> str | None:
    """Repo-local pre-PR check command for prompts and diagnostics (#2796).

    Preference order is specific -> generic. ``scripts.pre_pr`` is RM-only, so
    other repositories need their own local entry point. Each candidate must pass
    a lightweight ``--help`` probe so staff runs do not advertise a broken command.
    """
    for _rel, argv in _pre_pr_candidate_commands(worktree, python_executable):
        if _pre_pr_probe_succeeded(worktree, argv, run=run, rel=_rel):
            return " ".join(argv)
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
_SUBMODULE_ATTEMPTS = 3
_SUBMODULE_RETRY_BACKOFF_S = (2.0, 5.0)


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


def _submodule_failure_retryable(detail: str) -> bool:
    """True when a submodule init error may clear after stale per-worktree lock removal (#4331 follow-up)."""
    lower = detail.lower()
    return "index.lock" in lower or ("unable to create" in lower and "file exists" in lower)


def _submodule_init_failure_response(detail: str) -> WorktreeBootstrapPreflight:
    """Build a terminal bootstrap failure for submodule update errors."""
    if _submodule_failure_retryable(detail):
        return WorktreeBootstrapPreflight(
            ok=False,
            attention=f"submodule init failed: {detail}",
            failure_class="submodule_lock",
            retryable=True,
            remediation=(
                "Concurrent staff runs or a stale submodule index.lock blocked init; "
                "re-dispatch after sibling runs finish or locks age out."
            ),
        )
    return WorktreeBootstrapPreflight(
        ok=False,
        attention=f"submodule init failed: {detail}",
        failure_class="submodule_init",
        retryable=False,
        remediation="Inspect submodule URLs and network access in the staff checkout before re-dispatching.",
    )


def _linked_worktree_common_git_dir(worktree: Path) -> Path | None:
    """Resolve ``<main>/.git`` from a worktree's ``gitdir:`` pointer when the tree exists partially."""
    dot_git = worktree / ".git"
    if not dot_git.is_file():
        return None
    try:
        content = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content.startswith("gitdir:"):
        return None
    target = content[len("gitdir:") :].strip()
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = (worktree / target_path).resolve()
    for ancestor in target_path.parents:
        if ancestor.name != "worktrees":
            continue
        common = ancestor.parent
        return common if common.is_dir() else None
    return None


def _submodule_clone_lock_git_dir(worktree: Path, run: _Run) -> Path | None:
    """Git directory that owns sibling worktrees for per-clone submodule serialization (#4435)."""
    main_root = _main_checkout_root(worktree, run)
    if main_root is not None:
        main_git = main_root / ".git"
        if main_git.is_dir():
            return main_git
    linked = _linked_worktree_common_git_dir(worktree)
    if linked is not None:
        return linked
    git_dir = _git_dir(worktree)
    if git_dir is not None:
        return git_dir
    if not (worktree / ".git").exists():
        return None
    try:
        proc = _run_checked(run, worktree, ["git", "rev-parse", "--git-common-dir"], timeout=30)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (worktree / path).resolve()
    return path if path.is_dir() else None


@contextmanager
def _submodule_bootstrap_clone_lock(worktree: Path, run: _Run) -> Iterator[None]:
    """Exclusive lock so only one staff run bootstraps submodules per base clone at a time (#4435)."""
    git_dir = _submodule_clone_lock_git_dir(worktree, run)
    if git_dir is None:
        yield
        return
    lock_path = git_dir / _SUBMODULE_INIT_LOCK_BASENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows has no fcntl
        fcntl = None  # type: ignore[assignment]
    if fcntl is None:
        yield
        return
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _bootstrap_submodules(
    worktree: Path,
    run: _Run,
    *,
    is_git_process_active: Callable[[Path], bool] | None = None,
) -> WorktreeBootstrapPreflight | None:
    """Run the submodule update with a bounded per-attempt timeout and bounded retries.

    Returns a failing ``WorktreeBootstrapPreflight`` on timeout/error, or ``None`` on success.
    Each attempt clears stale locks in the worktree gitdir (including ``worktrees/<id>/modules``).
    """
    command = _submodule_update_command(worktree, run)
    last_timed_out = False
    last_detail = ""
    with _submodule_bootstrap_clone_lock(worktree, run):
        for attempt in range(_SUBMODULE_ATTEMPTS):
            clean_stale_git_locks(worktree, run=run, is_git_process_active=is_git_process_active)
            try:
                submodule = _run_checked(run, worktree, command, timeout=_SUBMODULE_ATTEMPT_TIMEOUT)
            except subprocess.TimeoutExpired:
                last_timed_out = True
                if attempt + 1 < _SUBMODULE_ATTEMPTS:
                    time.sleep(_SUBMODULE_RETRY_BACKOFF_S[min(attempt, len(_SUBMODULE_RETRY_BACKOFF_S) - 1)])
                continue
            if submodule.returncode != 0:
                last_detail = (submodule.stderr or submodule.stdout or "unknown git submodule error").strip()
                if attempt + 1 < _SUBMODULE_ATTEMPTS and _submodule_failure_retryable(last_detail):
                    log.warning(
                        "submodule update attempt %s failed (lock/checkout); retrying after lock cleanup",
                        attempt + 1,
                    )
                    time.sleep(_SUBMODULE_RETRY_BACKOFF_S[min(attempt, len(_SUBMODULE_RETRY_BACKOFF_S) - 1)])
                    continue
                return _submodule_init_failure_response(last_detail)
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
    if last_detail:
        return _submodule_init_failure_response(last_detail)
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


def _lock_age_seconds(lock: Path) -> float:
    """Seconds since the lock file was last modified; 0 when unknown."""
    try:
        return max(0.0, time.time() - lock.stat().st_mtime)
    except OSError:
        return 0.0


def _lock_held_by_live_process(lock: Path) -> bool:
    """True when a live process still has ``lock`` open (#3935 follow-up)."""
    try:
        import psutil
    except ImportError:
        return False

    try:
        resolved = str(lock.resolve())
    except OSError:
        resolved = str(lock)

    for proc in psutil.process_iter(attrs=["open_files"]):
        try:
            open_files = proc.info.get("open_files") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for entry in open_files:
            path = getattr(entry, "path", "") or ""
            if path == resolved:
                return True
    return False


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
        for lock_name in STALE_LOCK_NAMES:
            for sublock in d.rglob(lock_name):
                if sublock.is_file():
                    candidates.add(sublock)
    return sorted(candidates)


def _is_git_worktree_modules_lock(lock: Path) -> bool:
    """True when ``lock`` sits under ``<commondir>/.git/worktrees/<name>/modules/**``."""
    try:
        resolved = lock.resolve()
    except OSError:
        resolved = lock
    for ancestor in resolved.parents:
        if ancestor.name != "modules":
            continue
        wt_name_dir = ancestor.parent
        worktrees_dir = wt_name_dir.parent
        if worktrees_dir.name == "worktrees" and worktrees_dir.parent.name == ".git":
            return True
        return False
    return False


def _is_under_linked_worktree_gitdir(lock: Path) -> bool:
    """True when ``lock`` is under ``<commondir>/.git/worktrees/<name>/`` (any file)."""
    try:
        resolved = lock.resolve()
    except OSError:
        resolved = lock
    for ancestor in resolved.parents:
        parent = ancestor.parent
        if parent.name == "worktrees" and parent.parent.name == ".git":
            return True
    return False


def _git_activity_paths_for_lock(
    lock: Path,
    *,
    worktree: Path,
    main_root: Path | None,
) -> tuple[Path, ...]:
    """Paths whose live git activity should block removing ``lock`` (#3935 follow-up).

    Submodule locks under ``.git/modules/**`` are scoped to the linked main checkout.
    Locks under ``.git/worktrees/<name>/modules/**`` are scoped to the staff worktree
    only. Path components named ``worktrees`` elsewhere (e.g. ``~/staff-worktrees/``)
    must not be treated as linked worktree gitdirs.
    """
    if _is_git_worktree_modules_lock(lock):
        return (worktree,)
    if "modules" in lock.parts:
        if main_root is not None:
            return (main_root,)
        return (worktree,)
    if _is_under_linked_worktree_gitdir(lock):
        if main_root is not None:
            return (worktree, main_root)
        return (worktree,)
    if main_root is not None and main_root != worktree:
        return (worktree, main_root)
    return (worktree,)


def clean_stale_git_locks(
    worktree: Path,
    *,
    run: _Run = subprocess.run,
    is_git_process_active: Callable[[Path], bool] | None = None,
    main_root: Path | None = None,
) -> list[Path]:
    """Detect and remove stale git/submodule lock files safely (#3935).

    Each lock is removed only when no live git process is active in the repository
    scope that owns the lock. Locks are never deleted when a live git holds them.
    """
    checker = is_git_process_active or is_git_process_active_in_repo
    locks = find_candidate_git_locks(worktree, run=run)
    if not locks:
        return []

    if main_root is None:
        main_root = _main_checkout_root(worktree, run)

    removed: list[Path] = []
    for lock in locks:
        if _lock_held_by_live_process(lock):
            log.warning("Preserved live git lock (file still open): %s", lock)
            continue
        scope = _git_activity_paths_for_lock(lock, worktree=worktree, main_root=main_root)
        age = _lock_age_seconds(lock)
        git_active = any(checker(path) for path in scope)
        if git_active and age < STALE_GIT_LOCK_MAX_AGE_S:
            log.warning(
                "Preserved live git lock (live git process in scope %s): %s",
                ", ".join(str(p) for p in scope),
                lock,
            )
            continue
        if git_active and age >= STALE_GIT_LOCK_MAX_AGE_S:
            log.info(
                "Removing aged git lock (%.0fs >= %ss) despite git activity in scope %s: %s",
                age,
                STALE_GIT_LOCK_MAX_AGE_S,
                ", ".join(str(p) for p in scope),
                lock,
            )
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
        failure = _bootstrap_submodules(worktree, run, is_git_process_active=is_git_process_active)
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

    pre_pr = resolve_repo_pre_pr_entrypoint(worktree, python_executable=python_executable, run=run)
    if not pre_pr:
        missing = (
            "no runnable repository pre-PR entry point (probed scripts/pre_pr.py, "
            "shared_scripts/pre_pr.py, scripts/verify_merge_gates.py and scripts/agent-check)"
        )
        if not role_requires_pre_pr_entrypoint(role):
            log.warning(
                "pre-PR scripts missing in %s; continuing because role %s does not open PRs",
                worktree,
                role,
            )
            return WorktreeBootstrapPreflight(
                ok=True,
                python_executable=target_python,
                venv_dir=venv_dir,
                pre_pr_advisory=f"{missing}; optional for role {role} (no open_pr / code-read-only)",
            )
        return WorktreeBootstrapPreflight(
            ok=False,
            attention=missing,
            python_executable=target_python,
            venv_dir=venv_dir,
            failure_class="environment_error",
            retryable=False,
            remediation=(
                "Ensure the repo checkout includes a runnable pre-PR script "
                "(scripts/pre_pr.py or shared_scripts/pre_pr.py after submodule init). "
                "Staff pytest venvs are not used for pre-PR; populate shared_scripts when needed."
            ),
        )

    return WorktreeBootstrapPreflight(
        ok=True, python_executable=target_python, pre_pr_entrypoint=pre_pr, venv_dir=venv_dir
    )
