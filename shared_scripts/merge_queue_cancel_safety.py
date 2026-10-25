"""Merge-queue cancel safety (Bob queue analysis / #2768)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from shared_scripts.ci_workflow_concurrency import normalize_queue_head_ref


class MergeQueueParseError(RuntimeError):
    """Live queue state could not be read safely (fail closed)."""


@dataclass(frozen=True)
class MergeQueueEntry:
    head_ref: str
    head_sha: str


def merge_queue_entries_from_graphql(payload: Mapping[str, Any]) -> list[MergeQueueEntry]:
    """Parse GraphQL ``repository.mergeQueue.entries`` (nodes/headCommit/oid)."""
    repo = payload.get("repository") or {}
    merge_queue = repo.get("mergeQueue") or {}
    entries_block = merge_queue.get("entries")
    if not isinstance(entries_block, Mapping):
        raise MergeQueueParseError("mergeQueue.entries missing or not an object")

    page_info = entries_block.get("pageInfo") or {}
    if page_info.get("hasNextPage"):
        raise MergeQueueParseError("mergeQueue.entries hasNextPage=true")

    total_count = int(entries_block.get("totalCount") or 0)
    nodes = entries_block.get("nodes") or []
    parsed: list[MergeQueueEntry] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        head_commit = node.get("headCommit") or {}
        oid = str(head_commit.get("oid") or "")
        if oid:
            parsed.append(MergeQueueEntry(head_ref="", head_sha=oid))

    if total_count > 0 and not parsed:
        raise MergeQueueParseError("mergeQueue.entries totalCount>0 but no headCommit.oid parsed")

    if total_count > len(parsed):
        raise MergeQueueParseError(
            "mergeQueue.entries totalCount exceeds parsed headCommit.oid count"
        )

    return parsed


def is_live_merge_queue_head(
    head_ref: str,
    head_sha: str,
    entries: Sequence[MergeQueueEntry],
) -> bool:
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
    if live_entries is None:
        return False
    if is_live_merge_queue_head(head_ref, head_sha, live_entries):
        return False
    # Live queue entries are SHA-only; without run head_sha we cannot prove supersession.
    if live_entries and not (head_sha or "").strip():
        return False
    return bool(head_ref or head_sha)
