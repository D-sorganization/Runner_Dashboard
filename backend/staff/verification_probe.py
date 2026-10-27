"""GitHub PR probe for staff run verification (``gh api`` from worker threads)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from staff.verification_types import GH_TIMEOUT_SECONDS, GitHubLookupError, PullRequest, ci_state
from staff.workspace import ORG

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class GhCliPrProbe:
    """Finds a run's PR with two scoped ``gh api`` REST calls (the PR, then its head's check runs).

    Uses the ``gh`` CLI so it works from the runner's plain worker threads, where the
    event-loop-bound ``gh_client`` is unreachable (same approach as ``consolidation``).
    """

    def __init__(self, org: str = ORG, run: Runner = subprocess.run) -> None:
        self.org, self._run = org, run

    def _api(self, path: str) -> Any:
        try:
            proc = self._run(
                ["gh", "api", path], capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubLookupError(f"gh api {path}: {exc}") from exc
        if proc.returncode != 0:
            raise GitHubLookupError((proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:300])
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise GitHubLookupError(f"gh api {path}: invalid JSON") from exc

    def _full(self, repo: str) -> str:
        return repo if "/" in repo else f"{self.org}/{repo}"

    def _with_ci(self, full: str, pr: dict[str, Any]) -> PullRequest:
        state = "merged" if pr.get("merged_at") else str(pr.get("state") or "open")
        checks = self._api(f"/repos/{full}/commits/{pr['head']['sha']}/check-runs?per_page=100")
        return PullRequest(number=int(pr["number"]), state=state, ci=ci_state(checks.get("check_runs") or []))

    def find(self, repo: str, branch: str) -> PullRequest | None:
        full = self._full(repo)
        owner = full.split("/", 1)[0]
        head = quote(f"{owner}:{branch}", safe=":")
        pulls = self._api(f"/repos/{full}/pulls?head={head}&state=all&per_page=1")
        return self._with_ci(full, pulls[0]) if pulls else None

    def remote_ahead_by(self, repo: str, branch: str, base: str = "main") -> int:
        """How many commits ``branch`` is ahead of ``base`` on the remote (0 if the branch is missing)."""
        full = self._full(repo)
        path = f"/repos/{full}/compare/{quote(base, safe='')}...{quote(branch, safe='')}"
        try:
            body = self._api(path)
        except GitHubLookupError as exc:
            msg = str(exc).lower()
            if "404" in msg or "not found" in msg:
                return 0
            raise
        return int(body.get("ahead_by") or 0)

    def get(self, repo: str, number: int) -> PullRequest:
        """PR ``number`` in ``repo`` with its head CI state (the Code Request acceptance check, #1605)."""
        full = self._full(repo)
        return self._with_ci(full, self._api(f"/repos/{full}/pulls/{int(number)}"))
