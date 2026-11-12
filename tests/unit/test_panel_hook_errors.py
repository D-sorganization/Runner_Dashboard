"""Panel stop-hook reply classification (#2899)."""

from __future__ import annotations

import pytest
from staff.panel_hook_errors import hook_error_reason, is_hook_error_reply


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "trusted_agent_hook.py: can't discover a git worktree for this session",
        "Cannot discover a git worktree under /tmp/staff_panel_ab12",
        "Agent hook checks failed before handoff:\n- ruff-format: failed",
        "Stop hook failed: exit 2",
    ],
)
def test_is_hook_error_reply_detects_stop_hook_failures(text: str) -> None:
    assert is_hook_error_reply(text) is True
    reason = hook_error_reason(text)
    assert reason.startswith("provider stop-hook error:")


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "Ada agrees with Brook.\nSTANCE: agree\nPOSITION: ship it",
        "We should discuss git worktree hygiene in the main repo, not as a hook failure.",
        "",
    ],
)
def test_is_hook_error_reply_ignores_normal_panel_turns(text: str) -> None:
    assert is_hook_error_reply(text) is False
