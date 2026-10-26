"""merge_group cancel safety: never cancel live merge-queue heads (#2764)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared_scripts.merge_queue_cancel_safety import (
    MergeQueueEntry,
    MergeQueueParseError,
    is_live_merge_queue_head,
    may_cancel_merge_group_run,
    merge_queue_entries_from_graphql,
)

LIVE_REF = "gh-readonly-queue/main/pr-77-abc1234567890abcdef0123456789abcdef01"  # pragma: allowlist secret
LIVE_SHA = "0000000000000000000000000000000000000abc"
OLD_REF = "gh-readonly-queue/main/pr-77-deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"  # pragma: allowlist secret
OLD_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdead"  # pragma: allowlist secret


def _live_entries() -> list[MergeQueueEntry]:
    return [MergeQueueEntry(head_ref="", head_sha=LIVE_SHA)]


def test_live_head_sha_is_never_approved_for_cancel() -> None:
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


def test_graphql_fixture_parses_head_commit_oid() -> None:
    fixture = Path(__file__).parent / "fixtures" / "merge_queue_entries_graphql.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))["data"]
    parsed = merge_queue_entries_from_graphql(payload)
    assert parsed == [MergeQueueEntry(head_ref="", head_sha=LIVE_SHA)]


def test_graphql_has_next_page_fails_closed() -> None:
    payload = {
        "repository": {
            "mergeQueue": {
                "entries": {
                    "totalCount": 1,
                    "pageInfo": {"hasNextPage": True},
                    "nodes": [{"headCommit": {"oid": LIVE_SHA}}],
                }
            }
        }
    }
    with pytest.raises(MergeQueueParseError, match="hasNextPage"):
        merge_queue_entries_from_graphql(payload)


def test_graphql_partial_oid_parse_fails_closed() -> None:
    payload = {
        "repository": {
            "mergeQueue": {
                "entries": {
                    "totalCount": 2,
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {"state": "MERGEABLE", "headCommit": {"oid": LIVE_SHA}},
                        {"state": "MERGEABLE", "headCommit": {}},
                    ],
                }
            }
        }
    }
    with pytest.raises(MergeQueueParseError, match="non-QUEUED"):
        merge_queue_entries_from_graphql(payload)


def test_graphql_queued_entries_without_oid_are_ignored() -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "merge_queue_entries_graphql_mixed_queued.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))["data"]
    parsed = merge_queue_entries_from_graphql(payload)
    assert parsed == [MergeQueueEntry(head_ref="", head_sha=LIVE_SHA)]


def test_merge_queue_run_without_head_sha_fails_closed_no_cancel() -> None:
    assert (
        may_cancel_merge_group_run(
            head_ref=LIVE_REF,
            head_sha="",
            live_entries=_live_entries(),
        )
        is False
    )


def test_graphql_total_count_without_nodes_fails_closed() -> None:
    payload = {
        "repository": {
            "mergeQueue": {
                "entries": {
                    "totalCount": 2,
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [],
                }
            }
        }
    }
    with pytest.raises(MergeQueueParseError, match="totalCount"):
        merge_queue_entries_from_graphql(payload)
