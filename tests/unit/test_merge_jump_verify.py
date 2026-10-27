"""Unit tests for merge-queue jump position verification (#2952 slice 3)."""

from __future__ import annotations

import pytest

from escalation.merge_jump import (
    EscalatePlan,
    parse_queue_depth,
    verify_jump_took_effect,
)

pytestmark = pytest.mark.unit

_PLAN = EscalatePlan(
    owner="D-sorganization",
    repo="Runner_Dashboard",
    pr=2908,
    node_id="PR_kwDO",
    head_sha="deadbeef",
)


def test_verify_jump_succeeds_at_front() -> None:
    assert verify_jump_took_effect({"position": 1, "jump": True}, queue_depth=27, plan=_PLAN) is None


def test_verify_jump_fails_at_tail() -> None:
    result = verify_jump_took_effect({"position": 27, "jump": True}, queue_depth=27, plan=_PLAN)
    assert result is not None
    assert result.jump_ignored is True
    assert result.ok is False
    assert "27 of 27" in result.message
    assert "24444089" in result.message


def test_verify_jump_fails_when_not_first_without_depth() -> None:
    result = verify_jump_took_effect({"position": 5}, queue_depth=None, plan=_PLAN)
    assert result is not None
    assert result.jump_ignored is True
    assert "position 5" in result.message


def test_parse_queue_depth() -> None:
    payload = (
        '{"data":{"repository":{"mergeQueue":{"entries":{"totalCount":27}}}}}'
    )
    assert parse_queue_depth(payload) == 27
