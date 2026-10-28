"""Where staff runs execute: repository discovery, worktrees and prompt assembly.

Pure path logic plus two git/gh subprocess helpers. Nothing here knows about
the run store or the provider CLIs.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import staff.worktree_hygiene as _wh
from platform_utils.wsl_paths import windows_repositories_root
from staff.git_hooks import install_pre_push_hook
from staff.git_identity import configure_worktree_git_identity
from staff.standards_registry import _BUILTIN
from staff.worktree_status import worktree_has_unpushed_commits  # noqa: F401  re-export (#2806)

finalize_run_worktree = _wh.finalize_run_worktree
persist_run_transcript = _wh.persist_run_transcript
prune_merged_branches = _wh.prune_merged_branches
prune_worktree_branch = _wh.prune_worktree_branch
sweep_stale_worktrees = _wh.sweep_stale_worktrees

log = logging.getLogger("dashboard.staff.workspace")

ORG = os.environ.get("GITHUB_ORG", "D-sorganization")

# The runner marks a run that exits without this line as failed (#1221), so it is the
# last sentence of both rule sets rather than a clause mid-paragraph (Barb's live test, 2026-09-27).
RESULT_CONTRACT = (
    "Output contract: the very last line of your final message must start with 'STAFF_RESULT:' followed by a "
    "one-sentence summary. A run that ends without that line is recorded as failed, even when the work is done."
)

FLEET_RULES = _BUILTIN.body

READ_ONLY_FLEET_RULES = (
    "Fleet rules (code-read-only run, #1659): read and report only; do not edit, create or delete files, "
    "do not commit, push branches or open, comment on or merge pull requests or issues; use the local "
    "dashboard API and read-only gh/git commands; never take an issue or PR labelled claim:local or under "
    "another agent's live lease. " + RESULT_CONTRACT
)

PLAYBOOK_MAX_CHARS = 16000
_TRIMMED_MARKER = "\n[trimmed for prompt budget]"

from staff.workspace_prompt import compose_prompt as compose_prompt  # noqa: F401, E402


def dashboard_api_note() -> str:
    """Where an unattended run reads live fleet facts (#1221)."""
    base = f"http://127.0.0.1:{os.environ.get('DASHBOARD_PORT', '8321')}/api"
    return (
        "Live fleet facts (machines online, runs in flight, holds, schedules, spend) come from the local "
        f"Runner Dashboard API, never from memory: `curl -s {base}/staff/summary` is the one-call brief; "
        f"`{base}/staff/board` and `{base}/staff/runs` have detail. Fleet actions send "
        '`-H "Authorization: Bearer $FLEET_API_TOKEN"`. '
        "Unconnected-tier agents report milestones with `fleetctl checkin "
        "{started,claimed,pushed,pr_opened,blocked,done} --repo <repo>` (spooled offline when unreachable)."
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


def _truncate_for_budget(text: str, max_tokens: int) -> str:
    """Return ``text`` truncated to ``max_tokens`` with a stable marker."""
    from staff.standards_registry import estimate_tokens

    if max_tokens <= 0:
        return _TRIMMED_MARKER.strip()
    if estimate_tokens(text) <= max_tokens:
        return text
    max_chars = max_tokens * 4
    if max_chars <= len(_TRIMMED_MARKER):
        return _TRIMMED_MARKER[:max_chars]
    keep = max_chars - len(_TRIMMED_MARKER)
    return text[:keep].rstrip() + _TRIMMED_MARKER


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
    _git(checkout, "fetch", "origin", "--prune", "--quiet")
    base = "origin/main"
    if start_ref:
        _git(checkout, "fetch", "origin", "--quiet", f"+refs/heads/{start_ref}:refs/remotes/origin/{start_ref}")
        base = f"origin/{start_ref}"
    _git(checkout, "config", "extensions.worktreeConfig", "true")
    _git(checkout, "worktree", "add", "-b", branch, str(worktree), base)
    if worktree.is_dir():
        configure_worktree_git_identity(worktree)
        install_pre_push_hook(worktree)


def add_run_worktree(checkout: Path, worktree: Path, branch: str, head_ref: str = "") -> None:
    """A run's worktree: from the PR head when the run fixes an existing PR (#1881), else origin/main."""
    if head_ref:
        add_worktree(checkout, worktree, branch, start_ref=head_ref)
    else:
        add_worktree(checkout, worktree, branch)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )  # noqa: S603


def remove_worktree(
    worktree: Path, checkout: Path | None = None, branch: str = "", *, preserved_transcript: Path | None = None
) -> bool:
    """Remove only when normal Git accepts; preserve nonempty non-Git directories."""
    return _wh.remove_worktree_checked(worktree, checkout, branch, preserved_transcript)
