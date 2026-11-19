"""Read-only GitHub snapshot adapter, reusing the existing shepherd query.

One GraphQL list request per repository. Local ``context`` supplies holds,
review history and ledger state; ``confirm`` must confirm required checks and
live PR metadata at the same SHA. These boundaries remain explicit so the
core never mistakes an aggregate rollup for branch-protection evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

from coordination.merge_shepherd_pass import OPEN_PRS_QUERY
from staff.review_arm.intake import Backoff
from staff.review_arm.policy import Candidate

# Preserve the shepherd snapshot contract and add completeness evidence.
LIST_QUERY = OPEN_PRS_QUERY.replace(
    "labels(first: 30) { nodes { name } }",
    "labels(first: 100) { pageInfo { hasNextPage } nodes { name } }",
).replace(
    "pullRequests(states: OPEN, first: 100) {\n      nodes",
    "pullRequests(states: OPEN, first: 100) {\n      pageInfo { hasNextPage }\n      nodes",
)


def query_snapshot(owner: str, repo: str) -> Mapping[str, Any]:
    """Called on a worker thread; shared client owns App auth and rate headers."""
    import gh_client

    async def fetch() -> Mapping[str, Any]:
        try:
            payload = await gh_client.post(
                "graphql",
                json={
                    "query": LIST_QUERY,
                    "variables": {"owner": owner, "name": repo},
                },
                idempotent=True,
            )
        except gh_client.GhRateLimited as exc:
            raise Backoff(f"GitHub rate limited; retry after {exc.retry_after_seconds}s") from exc
        if not isinstance(payload, Mapping) or payload.get("errors"):
            raise OSError("GitHub snapshot unavailable")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise OSError("GitHub snapshot has no data")
        return data

    return asyncio.run(fetch())


class GitHubSource:
    def __init__(
        self,
        *,
        context: Callable[[Candidate], Candidate],
        confirm: Callable[[Candidate], Candidate],
        query: Callable[[str, str], Mapping[str, Any]] = query_snapshot,
    ) -> None:
        self._query = query
        self._context = context
        self._confirm = confirm

    def list_open(self, repo: str) -> Iterable[Candidate]:
        Candidate(repo, 1, "0" * 40)
        data = self._query("D-sorganization", repo)
        connection = data["repository"]["pullRequests"]
        if connection.get("pageInfo", {}).get("hasNextPage", True):
            raise OSError("open PR snapshot incomplete; no actions planned")
        for node in connection["nodes"]:
            labels = node.get("labels", {})
            c = Candidate(
                repo,
                int(node["number"]),
                node["headRefOid"],
                title=str(node.get("title", "")),
                draft=node.get("isDraft", True),
                mergeable=str(node.get("mergeable", "UNKNOWN")),
                merge_state=str(node.get("mergeStateStatus", "UNKNOWN")),
                labels=tuple(str(row["name"]) for row in labels.get("nodes", [])),
                armed=bool(node.get("autoMergeRequest")),
                in_merge_queue=bool(node.get("mergeQueueEntry")),
            )
            enriched = self._context(c)
            if (enriched.repo, enriched.pr, enriched.head_sha) != (c.repo, c.pr, c.head_sha):
                raise ValueError("context changed the snapshot target")
            if labels.get("pageInfo", {}).get("hasNextPage", True):
                enriched = replace(enriched, holds_available=False)
            yield enriched

    def confirm(self, candidate: Candidate) -> Candidate:
        return self._confirm(candidate)
