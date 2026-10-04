"""Where staff runs execute: repository discovery, worktrees and prompt assembly.

Pure path logic plus two git/gh subprocess helpers. Nothing here knows about
the run store or the provider CLIs.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from platform_utils.wsl_paths import windows_repositories_root
from staff import cli_projects
from staff.roles import RoleSpec

log = logging.getLogger("dashboard.staff.workspace")

ORG = os.environ.get("GITHUB_ORG", "D-sorganization")

# The runner marks a run that exits without this line as failed (#1221), so it is the
# last sentence of both rule sets rather than a clause mid-paragraph (Barb's live test, 2026-09-27).
RESULT_CONTRACT = (
    "Output contract: the very last line of your final message must start with 'STAFF_RESULT:' followed by a "
    "one-sentence summary. A run that ends without that line is recorded as failed, even when the work is done."
)

FLEET_RULES = (
    "Fleet rules: work only inside this worktree; TDD, DbC, LoD, DRY; commit with a Conventional Commits "
    "subject; if docs/development/HANDOFF.md exists update it in the same commit; push the branch, open a "
    "ready pull request (not draft), arm auto-merge via automerge_guard, and end session (pr_lifecycle: arm_and_exit); "
    "never merge, never force-push, never touch other worktrees or host configuration; "
    "never take an issue or PR labelled claim:local or under another agent's live lease; before choosing an "
    "issue, skip it if an open pull request already references it (gh pr list --state open --search '<number>'); "
    "never file bulk "
    "remediation issues (report findings in one issue or the PR instead); "
    "include the PR URL, if you opened one, in your STAFF_RESULT summary. " + RESULT_CONTRACT
)

READ_ONLY_FLEET_RULES = (
    "Fleet rules (code-read-only run, #1659): read and report only; do not edit, create or delete files, "
    "do not commit, push branches or open, comment on or merge pull requests or issues; use the local "
    "dashboard API and read-only gh/git commands; never take an issue or PR labelled claim:local or under "
    "another agent's live lease. " + RESULT_CONTRACT
)

PLAYBOOK_MAX_CHARS = 16000


def dashboard_api_note() -> str:
    """Where an unattended run reads live fleet facts (Barb's live test, 2026-09-27).

    Runs start in a fresh worktree, often with no repository at all, so without
    this line an agent asked for "machines online" has no source and either
    guesses or gives up. GETs from loopback need no token; fleet actions send the
    run's ``FLEET_API_TOKEN`` (minted per run in ``staff.runner``).
    """
    base = f"http://127.0.0.1:{os.environ.get('DASHBOARD_PORT', '8321')}/api"
    return (
        "Live fleet facts (machines online, runs in flight, holds, schedules, spend) come from the local "
        f"Runner Dashboard API, never from memory: `curl -s {base}/staff/summary` is the one-call brief; "
        f"`{base}/staff/board` and `{base}/staff/runs` have detail. Fleet actions send "
        '`-H "Authorization: Bearer $FLEET_API_TOKEN"`.'
    )


def repos_roots() -> list[Path]:
    """Directories that may contain fleet checkouts (first match wins)."""
    configured = os.environ.get("STAFF_REPOS_ROOT")
    roots = [Path(p).expanduser() for p in configured.split(os.pathsep) if p] if configured else []
    roots += _default_repos_roots(Path.home())
    return [r for r in roots if r.is_dir()]


def _default_repos_roots(home: Path) -> list[Path]:
    """Built-in checkout roots: the WSL home's, then the Windows profile's ``Repositories``."""
    return [
        home / "Repositories",
        home / "actions-runners" / "repos",
        windows_repositories_root(),
    ]


def rm_root() -> Path | None:
    """Locate a Repository_Management checkout for the lease ritual scripts."""
    override = os.environ.get("STAFF_RM_ROOT")
    if override:
        p = Path(override).expanduser()
        return p if (p / "scripts" / "check_agent_claim.py").is_file() else None
    for root in repos_roots():
        for name in ("Repository_Management-main", "Repository_Management"):
            cand = root / name
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


def git_common_dir(worktree: Path) -> Path:
    """Absolute git common dir of ``worktree`` (where a linked worktree's commits land).

    Post: returns ``worktree`` itself when it is not inside a git repository, so a
    sandbox scoped to the result never widens beyond the run's own directory.
    """
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
            cwd=str(worktree),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return worktree
    out = proc.stdout.strip()
    return Path(out) if out else worktree


def write_policy_file(provider: str, text: str) -> Path:
    """Write a provider's generated permission policy outside every worktree (#1586).

    Post: ``<staff worktrees root>/.policies/<provider>.toml`` holds exactly
    ``text``; it sits outside the run's checkout so no agent can commit it.
    """
    path = staff_worktrees_root() / ".policies" / f"{provider}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return path


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
            [
                "gh",
                "repo",
                "clone",
                f"{ORG}/{repo}",
                str(dest),
                "--",
                "--filter=blob:none",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=900,
        )
    return dest


def add_worktree(checkout: Path, worktree: Path, branch: str, *, start_ref: str = "") -> None:
    """``git fetch`` then ``git worktree add -b <branch> <worktree> origin/<start_ref or main>``.

    ``start_ref`` names an existing remote branch (a PR head, #1881); it is fetched
    explicitly so a blobless clone has it even when it is not in the default refspec.
    """
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(checkout, "fetch", "origin", "--quiet")
    base = "origin/main"
    if start_ref:
        _git(checkout, "fetch", "origin", "--quiet", f"+refs/heads/{start_ref}:refs/remotes/origin/{start_ref}")
        base = f"origin/{start_ref}"
    _git(checkout, "worktree", "add", "-b", branch, str(worktree), base)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )  # noqa: S603


def worktree_has_unpushed_commits(worktree: Path, base_ref: str = "origin/main") -> bool:
    """Return True if worktree has commits ahead of base_ref or uncommitted changes."""
    if not worktree.exists() or not worktree.is_dir():
        return False
    try:
        status_res = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if status_res.returncode == 0 and status_res.stdout.strip():
            return True
        # Try checking against upstream tracking branch first, fallback to base_ref
        log_res = subprocess.run(  # noqa: S603
            ["git", "rev-list", "@{u}..HEAD", "--count"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if log_res.returncode == 0:
            count = int(log_res.stdout.strip() or "0")
            return count > 0

        fallback_res = subprocess.run(  # noqa: S603
            ["git", "rev-list", f"{base_ref}..HEAD", "--count"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if fallback_res.returncode == 0:
            count = int(fallback_res.stdout.strip() or "0")
            return count > 0
    except Exception:  # noqa: BLE001
        return True
    return False


def remove_worktree(worktree: Path, checkout: Path | None = None) -> None:
    """Safely remove a git worktree."""
    if checkout is not None and checkout.exists():
        try:
            subprocess.run(  # noqa: S603
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=str(checkout),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:  # noqa: BLE001
            pass
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    if os.path.normpath(str(worktree.parent)) == os.path.normpath(str(staff_worktrees_root())):
        try:
            cli_projects.remove_worktree_project(worktree, staff_worktrees_root(), os.environ)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to remove CLI project for worktree %s: %s", worktree, exc)


def compose_prompt(
    role: RoleSpec,
    *,
    repo: str,
    target_ref: str,
    operator_prompt: str,
    branch: str,
    lease_note: str = "",
    consolidation: str = "",
    focus: str = "",
    chat_turn: bool = False,
) -> str:
    """Assemble the agent prompt from the role, the target and the fleet rules or reply contract.

    Kept deliberately plain: role instructions (from RM), the playbook path,
    the target, the PR-consolidation decision when the role has one (#1213),
    and the non-negotiable fleet rules for an unattended run (or reply contract for chat).
    """
    target = target_ref or "free-form task"
    if chat_turn:
        parts = [
            f"You are the fleet staff role '{role.title}' ({role.name}) in a conversation on the Runner Dashboard.",
        ]
    else:
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
        if chat_turn:
            parts.append(f"Repository context: {ORG}/{repo}. Target: {target}.")
        elif role.code_read_only:
            parts.append(f"Repository: {ORG}/{repo}. Target: {target}. You are in a read-only working copy.")
        else:
            parts.append(
                f"Repository: {ORG}/{repo}. Target: {target}. You are in an isolated git worktree on branch {branch}."
            )
    else:
        parts.append(f"Target: {target}.")
    if operator_prompt:
        parts.append("Task from the operator:\n" + operator_prompt.strip())
    if lease_note:
        parts.append(lease_note)
    if consolidation and not chat_turn:
        parts.append(consolidation)
    if focus and not chat_turn:
        parts.append(focus)

    if chat_turn:
        from staff.reply_contract import get_chat_contract_text

        contract_rel = (
            str(role.chat.get("contract") or "staff/prompts/_chat_contract.md")
            if role.chat
            else "staff/prompts/_chat_contract.md"
        )
        parts.append(get_chat_contract_text(contract_rel))
    else:
        parts.append(dashboard_api_note())
        parts.append(READ_ONLY_FLEET_RULES if role.code_read_only else FLEET_RULES)

    return "\n\n".join(parts)
