"""Prune stale agent git worktrees under fleet repo checkouts (Refs #2550).

Candidates must be clean (no uncommitted or unpushed changes), at least ``min_age_hours``
old, on a branch merged into ``origin/main`` or whose upstream is gone (closed/abandoned),
and not on the protected basename list (``pr-10507``, ``pr-10633`` by default). Removal
uses ``git worktree remove`` without ``--force``.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.agent_worktree_hygiene")

DEFAULT_PROTECTED_WORKTREES: tuple[str, ...] = ("pr-10507", "pr-10633")
DEFAULT_MIN_AGE_HOURS = 72.0


@dataclass(frozen=True)
class WorktreeEntry:
    path: Path
    branch: str


def _git(cwd: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
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


def _primary_worktree(checkout: Path) -> Path | None:
    listed = _parse_worktree_list(checkout)
    return listed[0].path if listed else None


def _is_protected(entry: WorktreeEntry, protected: tuple[str, ...]) -> bool:
    haystack = f"{entry.path.name}/{entry.branch}"
    return any(token in haystack for token in protected)


def _worktree_clean(worktree: Path) -> bool:
    """True when the linked worktree has no local changes and nothing ahead of main."""
    status = _git(worktree, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
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
    primary = _primary_worktree(checkout)
    if primary is not None and wt.path.resolve() == primary.resolve():
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
    removed = 0
    for checkout in repo_checkouts:
        if not (checkout / ".git").exists():
            continue
        _git(checkout, "fetch", "origin", "--prune", "--quiet")
        for entry in _parse_worktree_list(checkout)[1:]:
            ok, reason = worktree_entry_is_eligible(
                checkout,
                entry,
                min_age_hours=min_age_hours,
                protected=protected,
            )
            item = {"repo": checkout.name, "path": str(entry.path), "branch": entry.branch, "reason": reason}
            if not ok:
                skipped.append(item)
                continue
            would_remove.append(item)
            log.info(
                "%s agent worktree %s (%s) branch=%s",
                "would remove" if not apply else "removing",
                entry.path,
                checkout.name,
                entry.branch,
            )
            if apply and _git_worktree_remove(checkout, entry.path):
                removed += 1
    return {
        "dry_run": not apply,
        "would_remove": would_remove,
        "skipped": skipped,
        "removed": removed,
    }


def discover_repo_checkouts(extra_roots: list[Path] | None = None) -> list[Path]:
    from staff.workspace import repos_roots  # noqa: PLC0415

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
