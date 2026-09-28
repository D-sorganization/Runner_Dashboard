"""Tests for inbox item sorting (Workstream G).

Sorting rule:
1. Severity: critical > high > medium > low
2. Approvals first within equal severity
3. Newest first within equal severity and source type
"""

from __future__ import annotations

import asyncio

import pytest
from staff import inbox
from staff.inbox import InboxItem, collect_inbox


@pytest.mark.unit
def test_inbox_sorting_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # Disable external / db collections and mock each source directly
    monkeypatch.setattr(inbox, "github_inbox_sources_enabled", lambda: False)
    monkeypatch.setattr(inbox, "_collect_auth_sign_ins", lambda: [])

    # Mock return values for internal collectors
    items = [
        # Medium approval - older
        InboxItem(
            id="appr_old",
            source="approval",
            title="Approve old",
            summary="summary",
            severity="medium",
            created_at="2026-09-27T10:00:00Z",
            link="/staff",
        ),
        # Medium approval - newer
        InboxItem(
            id="appr_new",
            source="approval",
            title="Approve new",
            summary="summary",
            severity="medium",
            created_at="2026-09-27T12:00:00Z",
            link="/staff",
        ),
        # Medium non-approval - newest of all mediums
        InboxItem(
            id="ni_newest",
            source="needs_input",
            title="Needs input newest",
            summary="summary",
            severity="medium",
            created_at="2026-09-27T14:00:00Z",
            link="/staff",
        ),
        # Critical escalation
        InboxItem(
            id="esc_crit",
            source="escalation",
            title="Escalated",
            summary="summary",
            severity="critical",
            created_at="2026-09-27T08:00:00Z",
            link="/staff",
        ),
        # High needs_input
        InboxItem(
            id="ni_high",
            source="needs_input",
            title="Needs input high",
            summary="summary",
            severity="high",
            created_at="2026-09-27T09:00:00Z",
            link="/staff",
        ),
        # High approval
        InboxItem(
            id="appr_high",
            source="approval",
            title="Approve high",
            summary="summary",
            severity="high",
            created_at="2026-09-27T09:00:00Z",
            link="/staff",
        ),
        # Low project_decision
        InboxItem(
            id="pd_low",
            source="project_decision",
            title="Project decision low",
            summary="summary",
            severity="low",
            created_at="2026-09-27T15:00:00Z",
            link="/projects",
        ),
    ]

    monkeypatch.setattr(inbox, "_collect_approvals", lambda s: [i for i in items if i.source == "approval"])
    monkeypatch.setattr(inbox, "_collect_needs_input", lambda *a: [i for i in items if i.source == "needs_input"])
    monkeypatch.setattr(inbox, "_collect_escalations", lambda s: [i for i in items if i.source == "escalation"])

    aggregate = asyncio.run(collect_inbox(c_store=None, r_store=None, w_store=None))

    result_ids = [it.id for it in aggregate.items]

    # Expected order:
    # 1. Critical: esc_crit
    # 2. High: appr_high (approval comes first), then ni_high
    # 3. Medium: appr_new (12:00 approval), then appr_old (10:00 approval), then ni_newest (non-approval)
    # 4. Low: pd_low (none here since github sources disabled, but if present it's last)
    assert result_ids == [
        "esc_crit",
        "appr_high",
        "ni_high",
        "appr_new",
        "appr_old",
        "ni_newest",
    ]
