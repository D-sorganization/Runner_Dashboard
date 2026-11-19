"""Arm-only GitHub capability for review-arm (automerge_guard semantics, Refs #4249).

The only mutation this module can send is ``enablePullRequestAutoMerge`` with
``expectedHeadOid`` pinned to the reviewed head. There is no merge, enqueue,
admin or REST-merge path, and no fallback to one: a refused arm is reported.
Before arming it re-reads the PR and refuses a moved head, a closed or draft
PR, a hold label or held PR, incomplete label data, and unacknowledged file
deletions (RD #4449: the guard's ``deletions-acknowledged`` label/body rule).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.review_arm.policy import Candidate, LiveHead, is_held

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import automerge_guard  # noqa: E402

log = logging.getLogger(__name__)

ORG = "D-sorganization"
MAX_FILE_PAGES = 30  # 3000 files; a larger diff is refused rather than partly checked

PR_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id number headRefOid state isDraft title body
      labels(first: 100) { pageInfo { hasNextPage } nodes { name } }
      autoMergeRequest { enabledAt }
    }
  }
}"""

ARM_MUTATION = """mutation($id: ID!, $oid: GitObjectID!) {
  enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: SQUASH, expectedHeadOid: $oid}) {
    pullRequest { headRefOid autoMergeRequest { enabledAt mergeMethod } }
  }
}"""

GraphQL = Callable[[str, dict[str, Any]], Mapping[str, Any]]
RestGet = Callable[[str], Any]


@dataclass(frozen=True)
class ArmOutcome:
    armed: bool
    detail: str


class GitHubArmer:
    """``graphql`` returns the response ``data``; ``rest_get`` performs GET only."""

    def __init__(self, *, graphql: GraphQL, rest_get: RestGet, owner: str = ORG) -> None:
        self._graphql = graphql
        self._rest_get = rest_get
        self._owner = owner

    def _read(self, repo: str, pr: int) -> Mapping[str, Any]:
        data = self._graphql(PR_QUERY, {"owner": self._owner, "name": repo, "number": pr})
        node = (data.get("repository") or {}).get("pullRequest")
        if not isinstance(node, Mapping):
            raise OSError(f"{repo}#{pr} could not be read")
        return node

    @staticmethod
    def _labels(node: Mapping[str, Any]) -> tuple[str, ...] | None:
        labels = node.get("labels") or {}
        if (labels.get("pageInfo") or {}).get("hasNextPage", True):
            return None
        return tuple(str(row["name"]) for row in labels.get("nodes") or [])

    def live(self, repo: str, pr: int) -> LiveHead:
        """Current PR state; incomplete label data raises (holds unknown)."""
        node = self._read(repo, pr)
        labels = self._labels(node)
        if labels is None:
            raise OSError(f"{repo}#{pr} labels incomplete; holds unknown")
        return LiveHead(
            head_sha=str(node.get("headRefOid") or ""),
            state=str(node.get("state") or ""),
            draft=bool(node.get("isDraft", True)),
            labels=labels,
            title=str(node.get("title") or ""),
        )

    def _removed_files(self, repo: str, pr: int) -> list[str] | None:
        removed: list[str] = []
        for page in range(1, MAX_FILE_PAGES + 1):
            rows = self._rest_get(f"/repos/{self._owner}/{repo}/pulls/{pr}/files?per_page=100&page={page}")
            if not isinstance(rows, list):
                return None
            removed += [str(r.get("filename")) for r in rows if r.get("status") == "removed"]
            if len(rows) < 100:
                return removed
        return None

    def arm(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> ArmOutcome:
        """Arm squash auto-merge on ``repo#pr`` pinned to ``head``, or explain the refusal.

        Pre: ``head`` is the full SHA an effective approve was recorded at.
        Post: at most one ``enablePullRequestAutoMerge``; ``armed`` is True only if
        GitHub reports auto-merge enabled at ``head`` (or it already was).
        """
        Candidate(repo, pr, head)
        node = self._read(repo, pr)
        labels = self._labels(node)
        refusal = ""
        if labels is None:
            refusal = "labels incomplete; holds unknown"
        elif hold := is_held(repo, pr, labels, str(node.get("title") or ""), holds):
            refusal = hold
        elif node.get("state") != "OPEN":
            refusal = f"not open ({node.get('state')})"
        elif node.get("isDraft", True):
            refusal = "draft"
        elif node.get("headRefOid") != head:
            refusal = f"head moved (reviewed {head[:8]}, now {str(node.get('headRefOid'))[:8]})"
        elif node.get("autoMergeRequest"):
            return ArmOutcome(True, "already armed")
        if refusal:
            return ArmOutcome(False, refusal)
        removed = self._removed_files(repo, pr)
        lowered = {label.strip().lower() for label in labels or ()}
        if removed is None:
            return ArmOutcome(False, "changed-file list unavailable; deletions unknown")
        if removed and not automerge_guard._deletions_acknowledged(lowered, str(node.get("body") or "")):
            shown = ", ".join(removed[:5])
            return ArmOutcome(False, f"diff deletes {len(removed)} tracked file(s) with no acknowledgement: {shown}")
        try:
            data = self._graphql(ARM_MUTATION, {"id": str(node["id"]), "oid": head})
        except Exception as exc:  # noqa: BLE001 — a refused arm is reported, never retried or merged
            log.warning("review-arm: arm refused on %s#%s: %s", repo, pr, exc)
            return ArmOutcome(False, f"arm refused: {exc}"[:300])
        result = ((data.get("enablePullRequestAutoMerge") or {}).get("pullRequest")) or {}
        if result.get("headRefOid") != head or not result.get("autoMergeRequest"):
            return ArmOutcome(False, "GitHub did not report auto-merge enabled at the reviewed head")
        log.info("review-arm: auto-merge armed on %s#%s @%s (squash)", repo, pr, head[:8])
        return ArmOutcome(True, "armed")
