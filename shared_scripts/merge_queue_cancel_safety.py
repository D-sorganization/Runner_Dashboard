"""Merge-queue cancel safety (Bob queue analysis / #2764).

Pre: ``entries`` is the live ``repository.mergeQueue.entries`` list when deciding
whether a bot may cancel a ``merge_group`` workflow run.
Post: live heads are never approved for cancel; unknown queue state fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from shared_scripts.ci_workflow_concurrency import normalize_queue_head_ref


@dataclass(frozen=True)
class MergeQueueEntry:
    head_ref: str
    head_sha: str


def merge_queue_entries_from_graphql(payload: Mapping[str, Any]) -> list[MergeQueueEntry]:
    """Parse GraphQL ``repository.mergeQueue.entries`` nodes."""
    repo = payload.get("repository") or {}
    merge_queue = repo.get("mergeQueue") or {}
    nodes = merge_queue.get("entries") or []
    entries: list[MergeQueueEntry] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        head_ref = str(node.get("headRefName") or node.get("head_ref") or "")
        head_sha = str(node.get("headSha") or node.get("head_sha") or "")
        if head_ref or head_sha:
            entries.append(MergeQueueEntry(head_ref=head_ref, head_sha=head_sha))
    return entries


def is_live_merge_queue_head(
    head_ref: str,
    head_sha: str,
    entries: Sequence[MergeQueueEntry],
) -> bool:
    """True when this head ref or SHA matches a live merge-queue entry."""
    norm_ref = normalize_queue_head_ref(head_ref)
    norm_sha = (head_sha or "").lower()
    for entry in entries:
        if norm_ref and norm_ref == normalize_queue_head_ref(entry.head_ref):
            return True
        if norm_sha and norm_sha == (entry.head_sha or "").lower():
            return True
    return False


def may_cancel_merge_group_run(
    *,
    head_ref: str,
    head_sha: str,
    live_entries: Sequence[MergeQueueEntry] | None,
) -> bool:
    """Return True only when the queue state is known and this head is not live."""
    if live_entries is None:
        return False
    if is_live_merge_queue_head(head_ref, head_sha, live_entries):
        return False
    return bool(head_ref or head_sha)
