"""Canned GitHub REST responses sized like a ~40-repo org.

No network, and nothing heavy imported: the fake ``gh`` CLI imports this on every spawn.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    import httpx

ORG = "D-sorganization"
N_REPOS = 40
N_RUNNERS = 20
N_RUNS = 10
RATELIMIT_REMAINING = "4900"
_TS = "2026-10-31T00:00:00Z"


def repo_payload(index: int) -> dict[str, Any]:
    """Return one repository object."""
    return {"id": index, "name": f"repo{index}", "full_name": f"{ORG}/repo{index}", "archived": False,
            "private": True, "default_branch": "main", "open_issues_count": 3, "pushed_at": _TS,
            "updated_at": _TS, "owner": {"login": ORG}}  # fmt: skip


def run_payload(repo: str, index: int) -> dict[str, Any]:
    """Return one workflow run (every third is in progress, every fourth failed)."""
    return {"id": 1000 + index, "name": "CI", "head_branch": "main", "event": "push",
            "status": "completed" if index % 3 else "in_progress",
            "conclusion": "success" if index % 4 else "failure", "head_sha": "a" * 40,
            "run_number": index, "created_at": _TS, "updated_at": _TS, "run_started_at": _TS,
            "repository": {"name": repo, "full_name": f"{ORG}/{repo}"}, "actor": {"login": "bot"}}  # fmt: skip


def _runner(index: int) -> dict[str, Any]:
    labels = [{"name": "self-hosted"}, {"name": "linux"}]
    return {"id": index, "name": f"runner-{index}", "os": "Linux", "status": "online",
            "busy": index % 2 == 0, "labels": labels}  # fmt: skip


def respond(url: str) -> tuple[int, Any]:
    """Return ``(status, json_body)`` for a GitHub REST URL."""
    parts = urlsplit(url)
    path = parts.path
    if int(parse_qs(parts.query).get("page", ["1"])[0]) > 1:
        return 200, []
    if re.fullmatch(r"/orgs/[^/]+/repos", path):
        return 200, [repo_payload(i) for i in range(N_REPOS)]
    if re.fullmatch(r"/orgs/[^/]+/actions/runners", path):
        return 200, {"total_count": N_RUNNERS, "runners": [_runner(i) for i in range(N_RUNNERS)]}
    match = re.fullmatch(r"/repos/[^/]+/([^/]+)/actions/runs", path)
    if match:
        runs = [run_payload(match.group(1), i) for i in range(N_RUNS)]
        return 200, {"total_count": N_RUNS, "workflow_runs": runs}
    if re.search(r"/actions/runs/\d+/jobs$", path):
        job = {"id": 1, "name": "test", "status": "completed", "conclusion": "success", "steps": []}
        return 200, {"total_count": 1, "jobs": [job]}
    if re.fullmatch(r"/repos/[^/]+/[^/]+/(pulls|issues|commits|branches|labels)", path):
        return 200, []
    return 200, {}


class StubGitHub:
    """httpx handler that answers from ``respond`` after a fixed latency and counts calls."""

    def __init__(self, latency_s: float) -> None:
        self.latency_s = latency_s
        self.calls = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        """Count the call, sleep ``latency_s`` and return the canned response."""
        import httpx  # imported lazily: the fake gh imports this module on every spawn

        self.calls += 1
        await asyncio.sleep(self.latency_s)
        url = str(request.url.copy_with(scheme="https", host="api.github.com"))
        status, body = respond(url)
        return httpx.Response(status, json=body, headers={"x-ratelimit-remaining": RATELIMIT_REMAINING})
