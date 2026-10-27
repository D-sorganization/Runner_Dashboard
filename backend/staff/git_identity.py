"""Git author/committer identity for staff lane worktrees and CLI subprocesses."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_DEFAULT_BOT_NAME = "d-sorggeneralagent[bot]"
_DEFAULT_BOT_EMAIL = "289268526+d-sorggeneralagent[bot]@users.noreply.github.com"


def staff_git_user_name() -> str:
    return os.environ.get("STAFF_GIT_USER_NAME", "").strip() or _DEFAULT_BOT_NAME


def staff_git_user_email() -> str:
    return os.environ.get("STAFF_GIT_USER_EMAIL", "").strip() or _DEFAULT_BOT_EMAIL


def staff_git_identity_env() -> dict[str, str]:
    """Environment overrides so git commits in staff child processes use the bot identity."""
    name = staff_git_user_name()
    email = staff_git_user_email()
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def configure_worktree_git_identity(worktree: Path) -> None:
    """Write worktree-local user.name/email (requires extensions.worktreeConfig on the checkout)."""
    name = staff_git_user_name()
    email = staff_git_user_email()
    for key, value in (("user.name", name), ("user.email", email)):
        subprocess.run(
            ["git", "config", "--worktree", key, value],
            cwd=str(worktree),
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )  # noqa: S603
