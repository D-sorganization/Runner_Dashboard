"""Roster alias for cursor lane checkouts (#4562)."""

from __future__ import annotations

import pytest
from coordination.pr_age_rule import roster_agent_id


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