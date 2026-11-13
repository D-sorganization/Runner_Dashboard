"""Prune stale agent git worktrees under fleet repo checkouts (Refs #2550).

Candidates must be clean (no uncommitted or unpushed changes), at least ``min_age_hours``
old, on a branch merged into ``origin/main`` or whose upstream is gone (closed/abandoned),
and not on the protected basename list (``pr-10507``, ``pr-10633`` by default). Removal
uses ``git worktree remove`` without ``--force``.
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
DEFAULT_MIN_AGE_HOURS = 72.0


@dataclass(frozen=True)
class WorktreeEntry:
    path: Path
    branch: str


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
    res = _git(checkout, "worktree", "list", "--porcelain")
    if res.returncode != 0:
        return []
    entries: list[WorktreeEntry] = []
    path: Path | None = None
    branch = ""
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ").strip())
            branch = ""
        elif line.startswith("branch refs/heads/") and path is not None:
            branch = line.removeprefix("branch refs/heads/").strip()
            entries.append(WorktreeEntry(path=path, branch=branch))
            path = None
    return entries


def _is_primary_checkout(worktree: Path) -> bool:
    """True for the main repo checkout (``.git`` is a directory, not a linked worktree file)."""
    return (worktree / ".git").is_dir()


def _is_protected(entry: WorktreeEntry, protected: tuple[str, ...]) -> bool:
    haystack = f"{entry.path.name}/{entry.branch}"
    return any(token in haystack for token in protected)


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
    ts = now if now is not None else time.time()
    try:
        age_h = (ts - wt.path.stat().st_mtime) / 3600.0
    except OSError:
        return False, "stat_failed"
    if age_h < min_age_hours:
        return False, "too_young"
    if not _worktree_clean(wt.path):
        return False, "dirty"
    if not _branch_merged_or_closed(checkout, wt.branch):
        return False, "branch_active"
    return True, "eligible"


def _git_worktree_remove(checkout: Path, path: Path) -> bool:
    res = _git(checkout, "worktree", "remove", str(path), timeout=180)
    if res.returncode != 0:
        log.warning(
            "git worktree remove refused %s: %s",
            path,
            (res.stderr or res.stdout or "").strip()[:300],
        )
        return False
    return not path.exists()


def _refresh_origin(checkout: Path) -> None:
    """Fetch and prune ``origin`` unless it is a local path; raises ``TimeoutExpired`` on a slow remote."""
    url_res = _git(checkout, "remote", "get-url", "origin")
    if url_res.returncode != 0:
        return
    origin_url = url_res.stdout.strip()
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


def plan_agent_worktree_prune(
    repo_checkouts: list[Path],
    *,
    apply: bool = False,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    protected: tuple[str, ...] = DEFAULT_PROTECTED_WORKTREES,
) -> dict[str, Any]:
    """Scan checkouts and optionally remove eligible agent worktrees."""
    would_remove: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    skipped_checkouts: list[dict[str, str]] = []
    removed = 0
    for checkout in repo_checkouts:
        if not (checkout / ".git").exists():
            continue
        try:
            _refresh_origin(checkout)
            entries = _parse_worktree_list(checkout)
        except subprocess.TimeoutExpired as exc:
            # Refs may be stale, so nothing in this checkout is judged or removed on this run.
            command = _git_subcommand(exc)
            log.warning("skipping checkout %s: git %s timed out after %ss", checkout, command, exc.timeout)
            skipped_checkouts.append(
                {"repo": checkout.name, "path": str(checkout), "reason": "git_timeout", "command": command}
            )
            continue
        for entry in entries:
            if _is_primary_checkout(entry.path):
                continue
            item = {"repo": checkout.name, "path": str(entry.path), "branch": entry.branch}
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
            git_dir = child / ".git"
            if not git_dir.exists():
                continue
            key = str(child.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(child)
    return out
