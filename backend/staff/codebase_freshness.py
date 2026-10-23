"""Local-only freshness checks for committed codebase snapshots."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def snapshot_is_stale(root: Path, indexed_commit: str | None) -> bool:
    """Compare an indexed commit with HEAD and the last fetched default branch.

    No network, file enumeration, or checkout changes are permitted. Without a
    cached remote default, freshness describes local HEAD only; it cannot prove
    the remote repository is up to date. Missing/unreadable HEAD fails stale.
    """
    if not isinstance(indexed_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", indexed_commit):
        return True
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_ALLOW_PROTOCOL="", GIT_NO_LAZY_FETCH="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def resolve(ref: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                capture_output=True,
                text=True,
                check=True,
                timeout=2,
                env=env,
            ).stdout.strip()
            return result if re.fullmatch(r"[0-9a-f]{40}", result) else None
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    if resolve("HEAD") != indexed_commit:
        return True
    for ref in ("refs/remotes/origin/HEAD", "refs/remotes/origin/main", "refs/remotes/origin/master"):
        remote = resolve(ref)
        if remote is not None:
            return remote != indexed_commit
    return False
