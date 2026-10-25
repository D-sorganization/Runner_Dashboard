"""merge_group cancel safety: never cancel live merge-queue heads (#2764)."""

from __future__ import annotations

from shared_scripts.merge_queue_cancel_safety import (
    MergeQueueEntry,
    is_live_merge_queue_head,
    may_cancel_merge_group_run,
    merge_queue_entries_from_graphql,
)

LIVE_REF = "gh-readonly-queue/main/pr-77-abc1234567890abcdef0123456789abcdef01"
LIVE_SHA = "abc1234567890abcdef0123456789abcdef01"
OLD_REF = "gh-readonly-queue/main/pr-77-deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
OLD_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _live_entries() -> list[MergeQueueEntry]:
    return [MergeQueueEntry(head_ref=LIVE_REF, head_sha=LIVE_SHA)]


def test_live_head_ref_is_never_approved_for_cancel() -> None:
    assert is_live_merge_queue_head(LIVE_REF, LIVE_SHA, _live_entries()) is True
    assert (
        may_cancel_merge_group_run(
            head_ref=LIVE_REF, head_sha=LIVE_SHA, live_entries=_live_entries()
        )
        is False
    )


def test_superseded_head_not_in_live_entries_may_cancel() -> None:
    assert is_live_merge_queue_head(OLD_REF, OLD_SHA, _live_entries()) is False
    assert (
        may_cancel_merge_group_run(
            head_ref=OLD_REF, head_sha=OLD_SHA, live_entries=_live_entries()
        )
        is True
    )


def test_unknown_queue_state_fails_closed_no_cancel() -> None:
    assert may_cancel_merge_group_run(head_ref=OLD_REF, head_sha=OLD_SHA, live_entries=None) is False


def test_graphql_entries_parser_reads_head_ref_and_sha() -> None:
    payload = {
        "repository": {
            "mergeQueue": {
                "entries": [
                    {"headRefName": LIVE_REF, "headSha": LIVE_SHA},
                ]
            }
        }
    }
    parsed = merge_queue_entries_from_graphql(payload)
    assert parsed == _live_entries()
