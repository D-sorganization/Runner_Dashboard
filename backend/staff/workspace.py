"""Where staff runs execute: repository discovery, worktrees and prompt assembly.

Pure path logic plus two git/gh subprocess helpers. Nothing here knows about
the run store or the provider CLIs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from staff.roles import RoleSpec

ORG = os.environ.get("GITHUB_ORG", "D-sorganization")

FLEET_RULES = (
    "Fleet rules: work only inside this worktree; TDD, DbC, LoD, DRY; commit with a Conventional Commits "
    "subject; if docs/development/HANDOFF.md exists update it in the same commit; push the branch and open a "
    "DRAFT pull request; never merge, never force-push, never touch other worktrees or host configuration; "
    "never take an issue or PR labelled claim:local or under another agent's live lease; before choosing an "
    "issue, skip it if an open pull request already references it (gh pr list --state open --search '<number>'); "
    "never file bulk "
    "remediation issues (report findings in one issue or the PR instead); "
    "when done, print a final line starting with 'STAFF_RESULT:' followed by a one-sentence summary and the "
    "PR URL if any."
)

PLAYBOOK_MAX_CHARS = 16000


def repos_roots() -> list[Path]:
    """Directories that may contain fleet checkouts (first match wins)."""
    configured = os.environ.get("STAFF_REPOS_ROOT")
    roots = [Path(p).expanduser() for p in configured.split(os.pathsep) if p] if configured else []
    home = Path.home()
    roots += [
        home / "Repositories",
        home / "actions-runners" / "repos",
        Path("/mnt/c/Users") / os.environ.get("USERNAME", "diete") / "Repositories",
    ]
    return [r for r in roots if r.is_dir()]


def rm_root() -> Path | None:
    """Locate a Repository_Management checkout for the lease ritual scripts."""
    override = os.environ.get("STAFF_RM_ROOT")
    if override:
        p = Path(override).expanduser()
        return p if (p / "scripts" / "check_agent_claim.py").is_file() else None
    for root in repos_roots():
        cand = root / "Repository_Management"
        if (cand / "scripts" / "check_agent_claim.py").is_file():
            return cand
    return None


def playbook_text(rel: str, limit: int = PLAYBOOK_MAX_CHARS) -> str:
    """Inline a role playbook from the RM checkout; the run's worktree is another repo, so it cannot read it."""
    root = rm_root()
    if root is None or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        return ""
    path = root / rel
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n[playbook truncated]"


def staff_worktrees_root() -> Path:
    configured = os.environ.get("STAFF_WORKTREES_ROOT")
    if configured:
        return Path(configured).expanduser()
    roots = repos_roots()
    base = roots[0] if roots else Path.home()
    return base / "_staff_worktrees"


def find_repo_checkout(repo: str) -> Path | None:
    for root in repos_roots():
        cand = root / repo
        if (cand / ".git").exists():
            return cand
    return None


def clone_repo(repo: str) -> Path:
    """Blobless clone into ``<first root>/_staff_clones/<repo>`` (idempotent)."""
    roots = repos_roots()
    base = (roots[0] if roots else Path.home()) / "_staff_clones"
    base.mkdir(parents=True, exist_ok=True)
    dest = base / repo
    if not (dest / ".git").exists():
        subprocess.run(  # noqa: S603
            ["gh", "repo", "clone", f"{ORG}/{repo}", str(dest), "--", "--filter=blob:none"],
            check=True,
            capture_output=True,
            text=True,
            timeout=900,
        )
    return dest


def add_worktree(checkout: Path, worktree: Path, branch: str) -> None:
    """``git fetch`` then ``git worktree add -b <branch> <worktree> origin/main``."""
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(checkout, "fetch", "origin", "--quiet")
    _git(checkout, "worktree", "add", "-b", branch, str(worktree), "origin/main")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, timeout=600)  # noqa: S603


def compose_prompt(
    role: RoleSpec,
    *,
    repo: str,
    target_ref: str,
    operator_prompt: str,
    branch: str,
    lease_note: str = "",
    consolidation: str = "",
) -> str:
    """Assemble the agent prompt from the role, the target and the fleet rules.

    Kept deliberately plain: role instructions (from RM), the playbook path,
    the target, the PR-consolidation decision when the role has one (#1213),
    and the non-negotiable fleet rules for an unattended run.
    """
    target = target_ref or "free-form task"
    parts = [
        f"You are the fleet staff role '{role.title}' ({role.name}) running unattended from the Runner Dashboard.",
    ]
    if role.playbook:
        text = playbook_text(role.playbook)
        if text:
            parts.append(f"Your playbook (Repository_Management/{role.playbook}):\n" + text)
        else:
            parts.append(
                f"Your playbook is Repository_Management/{role.playbook}; read it first if it is available in this "
                "checkout or as a sibling repository."
            )
    if role.instructions:
        parts.append("Role instructions:\n" + role.instructions.strip())
    if repo:
        parts.append(
            f"Repository: {ORG}/{repo}. Target: {target}. You are in an isolated git worktree on branch {branch}."
        )
    else:
        parts.append(f"Target: {target}.")
    if operator_prompt:
        parts.append("Task from the operator:\n" + operator_prompt.strip())
    if lease_note:
        parts.append(lease_note)
    if consolidation:
        parts.append(consolidation)
    parts.append(FLEET_RULES)
    return "\n\n".join(parts)
