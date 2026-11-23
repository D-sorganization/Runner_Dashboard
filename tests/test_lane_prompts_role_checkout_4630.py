"""Regression for #4630: lane checkout must not substitute a different role."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _seed_review_and_backlog_lanes(store) -> None:
    store.register_assignment(
        lane_id="codex-node-b-2",
        role="backlog-completion",
        repos=["Tools"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="gpt-6.1-sol",
        max_children=1,
    )
    store.register_assignment(
        lane_id="codex-ct-test",
        role="acceptance-review",
        repos=["Tools"],
        release_sha="abc",
        package_hash="pkg",
        subagent_model="gpt-6.1-sol",
        max_children=1,
    )


def test_checkout_with_lane_id_and_requested_role_must_not_substitute(tmp_path: Path) -> None:
    """Requesting acceptance-review on a backlog lane must fail closed, not return backlog-completion."""
    from coordination.lane_prompts import LaneRoleUnavailableError, LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    _seed_review_and_backlog_lanes(store)

    with pytest.raises(LaneRoleUnavailableError) as excinfo:
        store.checkout(
            session_id="codex-cli-node-b-20261122-170757",
            principal="agent-codex",
            idempotency_key="idem-review-4630",
            lane_id="codex-node-b-2",
            role="acceptance-review",
            host="node-b",
        )
    err = excinfo.value
    assert err.requested_role == "acceptance-review"
    assert err.assigned_role == "backlog-completion"
    assert "backlog-completion" in err.reason


def test_checkout_honours_requested_role_when_unclaimed_slot_exists(tmp_path: Path) -> None:
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    _seed_review_and_backlog_lanes(store)

    receipt = store.checkout(
        session_id="codex-cli-ct-20261122-180000",
        principal="agent-codex",
        idempotency_key="idem-review-free",
        role="acceptance-review",
        provider="codex",
        host="ct",
    )
    assert receipt["role"] == "acceptance-review"
    assert receipt["requested_role"] == "acceptance-review"
    assert receipt["lane_id"] == "codex-ct-test"


def test_checkout_role_suffix_is_normalized(tmp_path: Path) -> None:
    from coordination.lane_prompts import LanePromptStore

    store = LanePromptStore(db_path=tmp_path / "lane_prompts.sqlite3")
    _seed_review_and_backlog_lanes(store)

    receipt = store.checkout(
        session_id="codex-cli-ct-20261122-180001",
        principal="agent-codex",
        idempotency_key="idem-review-suffix",
        role="acceptance-review:codex",
        provider="codex",
        host="ct",
    )
    assert receipt["role"] == "acceptance-review"
    assert receipt["requested_role"] == "acceptance-review"