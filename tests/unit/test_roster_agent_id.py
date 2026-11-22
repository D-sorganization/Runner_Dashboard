"""Roster alias for cursor lane checkouts (#4562)."""

from __future__ import annotations

import pytest
from coordination.pr_age_rule import lease_agent_id, roster_agent_id


@pytest.mark.unit
@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("cursor", "cursor-agent"),
        ("CURSOR", "cursor-agent"),
        ("agy", "antigravity"),
        ("codex", "codex"),
    ],
)
def test_roster_agent_id(agent: str, expected: str) -> None:
    assert roster_agent_id(agent) == expected


_ROSTER = ("codex", "cursor-agent", "gemini", "antigravity")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("cursor", "cursor-agent"),
        ("CURSOR", "cursor-agent"),
        ("agy", "antigravity"),
        ("gemini", "gemini"),
        ("codex", "codex"),
        ("nope", None),
    ],
)
def test_lease_agent_id_maps_lane_aliases_and_keeps_roster_names(agent: str, expected: str | None) -> None:
    assert lease_agent_id(agent, _ROSTER) == expected
