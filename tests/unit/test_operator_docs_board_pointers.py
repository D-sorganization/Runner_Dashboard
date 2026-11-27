"""Live operator docs name the current boards (issue #4685).

Dated history may still mention #1576, #2376, and #2539. Current routing
instructions may not. ``docs/coordination-api.md`` is excluded: open PRs
#4635 and #4636 already edit it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

MESSENGER = ROOT / "docs" / "agents" / "messenger-agents.md"
DESK_CLI = ROOT / "docs" / "agents" / "desk-cli-lane-claimability.md"
GROK = ROOT / "docs" / "agents" / "grok.md"
SENTINEL = ROOT / "docs" / "runbooks" / "hub-sentinel.md"
RELEASE = ROOT / "docs" / "runbooks" / "release-target-qualification.md"

ORDERS_BOARD = "#2851"
MESSAGE_CONFIG = "config/agent_communication.json"
FRIDAY_AGENDA = "/api/priorities"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_messenger_doc_separates_orders_messages_and_friday_agenda() -> None:
    text = _text(MESSENGER)
    exists = MESSENGER.relative_to(ROOT).as_posix()
    for needle in (ORDERS_BOARD, MESSAGE_CONFIG, FRIDAY_AGENDA):
        assert needle in text, f"{exists} is missing {needle}"
    current = text.split("## Slice 1: Dry Run", 1)[0]
    assert "issue #2376 (successor of #1576)" not in current
    assert "(RM #2376)" not in current


@pytest.mark.unit
def test_claim_instructions_use_orders_board() -> None:
    desk = _text(DESK_CLI)
    grok = _text(GROK)
    release = _text(RELEASE)
    assert ORDERS_BOARD in desk
    assert "#1576" not in desk
    assert "RM #2851" in grok
    assert "#2539" not in grok
    assert ORDERS_BOARD in release
    assert "#2539" not in release


@pytest.mark.unit
def test_hub_sentinel_runbook_points_at_configured_message_board() -> None:
    text = _text(SENTINEL)
    issue_line = next(line for line in text.splitlines() if "HUB_SENTINEL_RM_ISSUE" in line)
    assert "2539" not in issue_line
    assert MESSAGE_CONFIG in text
