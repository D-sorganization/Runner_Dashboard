"""Where fleet repo checkouts live on this node.

Kept apart from ``staff.workspace`` because ``deploy/prune_agent_worktrees.py`` runs under the
hosts' system python3.10, outside the dashboard virtualenv: this module and everything it imports
must stay stdlib-only and free of 3.11+ constructs (guarded by
``tests/deploy/test_prune_agent_worktrees_cli.py``).
"""

from __future__ import annotations

import os
from pathlib import Path

from platform_utils.wsl_paths import windows_repositories_root

REPOS_ROOT_ENV = "STAFF_REPOS_ROOT"


def default_repos_roots(home: Path, windows_root: Path) -> list[Path]:
    """Built-in checkout roots: the WSL home's, then the Windows profile's ``Repositories``."""
    return [
        home / "Repositories",
        home / "actions-runners" / "repos",
        windows_root,
    ]


def existing_repos_roots(configured: str | None, defaults: list[Path]) -> list[Path]:
    """Configured roots (``os.pathsep``-separated) then ``defaults``, keeping only real directories."""
    roots = [Path(p).expanduser() for p in configured.split(os.pathsep) if p] if configured else []
    return [r for r in roots + defaults if r.is_dir()]


def repos_roots() -> list[Path]:
    """Directories that may contain fleet checkouts (first match wins)."""
    defaults = default_repos_roots(Path.home(), windows_repositories_root())
    return existing_repos_roots(os.environ.get(REPOS_ROOT_ENV), defaults)
