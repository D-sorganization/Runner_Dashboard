"""Decision table for refusing gh pr create on drained repositories (#3915 slice 1)."""

from __future__ import annotations

import pytest
from drain_guard import EXEMPT_CLASSES, guard_decision

DRAINED = {"drained": True, "reason": "active"}
LIFTED = {"drained": False, "reason": "below_threshold"}


@pytest.mark.parametrize(
    ("status", "title", "labels", "allow", "reason"),
    [
        (None, "feat: new thing", [], True, "hub_unreachable_fail_open"),
        (LIFTED, "feat: new thing", [], True, "not_drained"),
        (DRAINED, "feat: new thing", [], False, "drained"),
        (DRAINED, "feat: x", ["ci"], True, "exempt:ci"),
        (DRAINED, "feat: x", ["Security"], True, "exempt:security"),
        (DRAINED, "ci: pin runner image", [], True, "exempt:ci"),
        (DRAINED, "ci(runner): fix label", [], True, "exempt:ci"),
        (DRAINED, "fix(security): bump certifi", [], True, "exempt:security"),
        (DRAINED, "fix(red-main): restore lint", [], True, "exempt:red-main"),
        (DRAINED, "feat: ci dashboard widget", [], False, "drained"),
        (DRAINED, "chore(cleanup): security notes", [], False, "drained"),
    ],
)
def test_guard_decision_table(
    status: dict[str, object] | None, title: str, labels: list[str], allow: bool, reason: str
) -> None:
    decision = guard_decision(status, title=title, labels=labels)
    assert (decision.allow, decision.reason) == (allow, reason)
    assert decision.allow or all(cls in decision.message for cls in EXEMPT_CLASSES)
