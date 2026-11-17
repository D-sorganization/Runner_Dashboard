"""Prune stale agent git worktrees under fleet repo checkouts (Refs #2550, #4440).

Candidates must be clean (no uncommitted or unpushed changes), at least ``min_age_hours``
old, on a branch merged into ``origin/main`` or whose upstream is gone (closed/abandoned),
and not on the protected basename list (``pr-10507``, ``pr-10633`` by default). Worktrees of
a private repo (``DEFAULT_PROTECTED_REPOS``) are never removed, and such a repo is not fetched.

Removal uses ``git worktree remove`` without ``--force``. The one exception is a tree with
submodules, which git refuses to remove otherwise: it is forced only after the tree and every
submodule checked out in it are shown to hold no local changes and no unpushed commits.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.repo_roots import repos_roots

log = logging.getLogger("dashboard.agent_worktree_hygiene")

DEFAULT_PROTECTED_WORKTREES: tuple[str, ...] = ("pr-10507", "pr-10633")
DEFAULT_PROTECTED_REPOS: tuple[str, ...] = ("work_private", "Tools_Private", "glass")
DEFAULT_MIN_AGE_HOURS = 72.0
_GITLINK_MODE = "160000"


@dataclass(frozen=True)
class WorktreeEntry:
    path: Path
    branch: str
    locked: bool = False


def _git(cwd: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
        env.pop(key, None)
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _parse_worktree_list(checkout: Path) -> list[WorktreeEntry]:
    """Every non-bare worktree of ``checkout``; a detached one has an empty ``branch``."""
    res = _git(checkout, "worktree", "list", "--porcelain")
    if res.returncode != 0:
        return []
    entries: list[WorktreeEntry] = []
    for block in res.stdout.split("\n\n"):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        paths = [ln.removeprefix("worktree ") for ln in lines if ln.startswith("worktree ")]
        if not paths or "bare" in lines:
            continue
        branches = [ln.removeprefix("branch refs/heads/") for ln in lines if ln.startswith("branch refs/heads/")]
        locked = any(ln == "locked" or ln.startswith("locked ") for ln in lines)
        entries.append(WorktreeEntry(path=Path(paths[0]), branch=branches[0] if branches else "", locked=locked))
    return entries


def _is_primary_checkout(worktree: Path) -> bool:
    """True for the main repo checkout (``.git`` is a directory, not a linked worktree file)."""
    return (worktree / ".git").is_dir()


def _is_protected(entry: WorktreeEntry, protected: tuple[str, ...]) -> bool:
    haystack = f"{entry.path.name}/{entry.branch}"
    return any(token in haystack for token in protected)


def _repo_name_from_url(origin_url: str) -> str:
    tail = origin_url.strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def is_protected_repo(checkout: Path, origin_url: str, protected_repos: tuple[str, ...]) -> bool:
    """True when the checkout folder or its origin is named as a protected (private) repo."""
    protected = {name.casefold() for name in protected_repos}
    return checkout.name.casefold() in protected or _repo_name_from_url(origin_url).casefold() in protected


def _gitlink_paths(worktree: Path) -> list[str] | None:
    """Submodule paths recorded in the index of ``worktree``; ``None`` when it cannot be read."""
    res = _git(worktree, "ls-files", "--stage", "-z")
    if res.returncode != 0:
        return None
    records = [rec for rec in res.stdout.split("\0") if rec.startswith(_GITLINK_MODE + " ") and "\t" in rec]
    return [rec.split("\t", 1)[1] for rec in records]


def _submodule_problem(worktree: Path) -> str:
    """Why a submodule below ``worktree`` blocks removal, or ``""`` when none does (fail-closed)."""
    links = _gitlink_paths(worktree)
    if links is None:
        return "submodule_unreadable"
    for rel in links:
        sub = worktree / rel
        if not (sub / ".git").exists():
            # Not initialised: git keeps nothing for it, so any file here is somebody's work.
            if sub.is_dir() and any(sub.iterdir()):
                return "submodule_dirty"
            continue
        status = _git(sub, "status", "--porcelain")
        if status.returncode != 0:
            return "submodule_unreadable"
        if status.stdout.strip():
            return "submodule_dirty"
        unpushed = _git(sub, "rev-list", "--count", "HEAD", "--all", "--not", "--remotes")
        if unpushed.returncode != 0:
            return "submodule_unreadable"
        if unpushed.stdout.strip() != "0":
            return "submodule_unpushed"
        nested = _submodule_problem(sub)
        if nested:
            return nested
    return ""


def _worktree_clean(worktree: Path) -> bool:
    """True when the linked worktree has no local changes and nothing ahead of main."""
    status = _git(worktree, "status", "--porcelain")
    if status.returncode != 0:
        return False
    dirty = [
        ln
        for ln in status.stdout.splitlines()
        if ln.strip() and not ln.strip().startswith(("?? .staff/", "?? .gemini/"))
    ]
    if dirty:
        return False
    for base in ("origin/main", "main"):
        ahead = _git(worktree, "rev-list", f"{base}..HEAD", "--count")
        if ahead.returncode == 0:
            return ahead.stdout.strip() == "0"
    return False


def _branch_merged_or_closed(checkout: Path, branch: str) -> bool:
    if not branch or branch in {"main", "master", "HEAD"}:
        return False
    branch_tip = _git(checkout, "rev-parse", branch)
    main_tip = _git(checkout, "rev-parse", "main")
    if branch_tip.returncode == 0 and main_tip.returncode == 0 and branch_tip.stdout.strip() == main_tip.stdout.strip():
        return True
    for base in ("origin/main", "main"):
        ancestor = _git(checkout, "merge-base", "--is-ancestor", branch, base)
        if ancestor.returncode == 0:
            return True
    track = _git(checkout, "for-each-ref", "--format=%(upstream:track)", f"refs/heads/{branch}")
    if track.returncode == 0 and ": gone]" in (track.stdout or ""):
        ahead = _git(checkout, "rev-list", f"origin/main..{branch}", "--count")
        return ahead.returncode == 0 and ahead.stdout.strip() == "0"
    return False


def worktree_entry_is_eligible(
    checkout: Path,
    entry: dict[str, Any] | WorktreeEntry,
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    protected: tuple[str, ...] = DEFAULT_PROTECTED_WORKTREES,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return (eligible, reason) for one linked worktree (not the primary checkout)."""
    wt = entry if isinstance(entry, WorktreeEntry) else WorktreeEntry(path=entry["path"], branch=entry["branch"])
    if _is_primary_checkout(wt.path):
        return False, "primary"
    if _is_protected(wt, protected):
        return False, "protected"
    if wt.locked:
        return False, "locked"
    if not wt.branch:
        return False, "detached"
    ts = now if now is not None else time.time()
    try:
        age_h = (ts - wt.path.stat().st_mtime) / 3600.0
    except OSError:
        return False, "stat_failed"
    if age_h < min_age_hours:
        return False, "too_young"
    if not _worktree_clean(wt.path):
        return False, "dirty"
    problem = _submodule_problem(wt.path)
    if problem:
        return False, problem
    if not _branch_merged_or_closed(checkout, wt.branch):
        return False, "branch_active"
    return True, "eligible"


def _git_worktree_remove(checkout: Path, path: Path) -> bool:
    links = _gitlink_paths(path)
    if links is None:
        log.warning("not removing %s: its index cannot be read", path)
        return False
    args = ["worktree", "remove"]
    if links:
        # git refuses any tree with submodules unless forced, so prove again that nothing is lost.
        problem = "dirty" if not _worktree_clean(path) else _submodule_problem(path)
        if problem:
            log.warning("not forcing removal of submodule worktree %s: %s", path, problem)
            return False
        args.append("--force")
    res = _git(checkout, *args, str(path), timeout=180)
    if res.returncode != 0:
        log.warning(
            "git worktree remove refused %s: %s",
            path,
            (res.stderr or res.stdout or "").strip()[:300],
        )
        return False
    return not path.exists()


def _origin_url(checkout: Path) -> str:
    res = _git(checkout, "remote", "get-url", "origin")
    return res.stdout.strip() if res.returncode == 0 else ""


def _refresh_origin(checkout: Path, origin_url: str) -> None:
    """Fetch and prune ``origin`` unless it is a local path; raises ``TimeoutExpired`` on a slow remote."""
    if not origin_url:
        return
    local_origin = origin_url.startswith("file:") or (origin_url.startswith("/") and Path(origin_url).exists())
    if not local_origin:
        _git(checkout, "fetch", "origin", "--prune", "--quiet")


def _git_subcommand(exc: subprocess.TimeoutExpired) -> str:
    """The git subcommand of a timed-out ``_git`` call (``git -C <cwd> <subcommand> ...``)."""
    cmd = exc.cmd if isinstance(exc.cmd, (list, tuple)) else []
    return str(cmd[3]) if len(cmd) > 3 else "unknown"


def _worktree_timeout_reason(worktree: Path, exc: subprocess.TimeoutExpired) -> str:
    """Log a timed-out git call for one worktree; return its ``skipped`` reason (``git_timeout:<subcommand>``)."""
    command = _git_subcommand(exc)
    log.warning("skipping worktree %s: git %s timed out after %ss", worktree, command, exc.timeout)
    return f"git_timeout:{command}"


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _linked_worktrees(checkout: Path, seen: set[str]) -> list[WorktreeEntry]:
    """Linked worktrees of ``checkout`` that no earlier checkout has already listed."""
    fresh: list[WorktreeEntry] = []
    for entry in _parse_worktree_list(checkout):
        key = _path_key(entry.path)
        if key in seen or _is_primary_checkout(entry.path):
            continue
        seen.add(key)
        fresh.append(entry)
    return fresh


def _count_by_reason(skipped: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in skipped:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    return dict(sorted(counts.items()))


def plan_agent_worktree_prune(
    repo_checkouts: list[Path],
    *,
    apply: bool = False,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    protected: tuple[str, ...] = DEFAULT_PROTECTED_WORKTREES,
    protected_repos: tuple[str, ...] = DEFAULT_PROTECTED_REPOS,
) -> dict[str, Any]:
    """Scan primary checkouts and optionally remove eligible agent worktrees.

    Each worktree is reported once, under the repo that owns it. A path in ``repo_checkouts``
    that is itself a linked worktree is ignored: its owner lists it.
    """
    would_remove: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    skipped_checkouts: list[dict[str, str]] = []
    removed = 0
    seen: set[str] = set()
    for checkout in repo_checkouts:
        if not _is_primary_checkout(checkout) or _path_key(checkout) in seen:
            continue
        seen.add(_path_key(checkout))
        try:
            origin_url = _origin_url(checkout)
            private = is_protected_repo(checkout, origin_url, protected_repos)
            if not private:
                _refresh_origin(checkout, origin_url)
            entries = _linked_worktrees(checkout, seen)
        except subprocess.TimeoutExpired as exc:
            # Refs may be stale, so nothing in this checkout is judged or removed on this run.
            command = _git_subcommand(exc)
            log.warning("skipping checkout %s: git %s timed out after %ss", checkout, command, exc.timeout)
            skipped_checkouts.append(
                {"repo": checkout.name, "path": str(checkout), "reason": "git_timeout", "command": command}
            )
            continue
        for entry in entries:
            item = {"repo": checkout.name, "path": str(entry.path), "branch": entry.branch}
            if private:
                skipped.append({**item, "reason": "private_repo"})
                continue
            try:
                ok, reason = worktree_entry_is_eligible(
                    checkout,
                    entry,
                    min_age_hours=min_age_hours,
                    protected=protected,
                )
            except subprocess.TimeoutExpired as exc:
                ok, reason = False, _worktree_timeout_reason(entry.path, exc)
            if not ok:
                skipped.append({**item, "reason": reason})
                continue
            would_remove.append({**item, "reason": reason})
            log.info(
                "%s agent worktree %s (%s) branch=%s",
                "would remove" if not apply else "removing",
                entry.path,
                checkout.name,
                entry.branch,
            )
            if not apply:
                continue
            try:
                removed += _git_worktree_remove(checkout, entry.path)
            except subprocess.TimeoutExpired as exc:
                skipped.append({**item, "reason": _worktree_timeout_reason(entry.path, exc)})
    return {
        "dry_run": not apply,
        "would_remove": would_remove,
        "skipped": skipped,
        "skipped_by_reason": _count_by_reason(skipped),
        "skipped_checkouts": skipped_checkouts,
        "removed": removed,
    }


def discover_repo_checkouts(extra_roots: list[Path] | None = None) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for root in repos_roots() + (extra_roots or []):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not _is_primary_checkout(child):
                # A linked worktree (``.git`` is a file) is listed by the checkout that owns it.
                continue
            key = str(child.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(child)
    return out
