"""Unit tests for Board Proposals source in staff inbox (WP-0.2, Issue #1475).

Validates:
- (a) open proposals appear as inbox items and are counted
- (b) decided or closed proposals are excluded
- (c) a store exception yields unavailable while the rest of the inbox still renders
- (d) caching within TTL avoids redundant store calls
- (e) item mapping preserves kind/source, title, severity, link, and created_at
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from cache_utils import cache_clear
from proposals.store import ProposalStoreError
from staff.conversations import reset_conversation_store
from staff.inbox import SourceStatus, collect_inbox
from staff.store import reset_store as reset_run_store
from staff.work_items import reset_work_item_store

MOCK_OPEN_PROPOSAL_1 = {
    "number": 101,
    "title": "[Proposal] Add automated database compaction",
    "body": """### Submitter / Source
human

### Target Repository / Repositories
Repository_Management

### Problem Statement
Database files grow indefinitely without routine compaction.

### Evidence and Context
Disk usage on WSL host reached 90%.

### Options Considered
- Manual diskpart
- Scheduled workflow

### Submitter's Lean
Scheduled workflow

### Estimated Effort
Low

### Urgency
Urgent
""",
    "state": "open",
    "created_at": "2026-09-25T10:00:00Z",
    "updated_at": "2026-09-25T11:00:00Z",
    "closed_at": None,
    "html_url": "https://github.com/D-sorganization/Repository_Management/issues/101",
    "labels": [{"name": "board:proposal"}, {"name": "needs-decision"}],
}

MOCK_OPEN_PROPOSAL_2 = {
    "number": 102,
    "title": "[Proposal] Emergency fix for runner credential leak",
    "body": """### Problem Statement
Credentials must be revoked immediately.

### Urgency
Emergency
""",
    "state": "open",
    "created_at": "2026-09-25T11:00:00Z",
    "updated_at": "2026-09-25T11:00:00Z",
    "closed_at": None,
    "html_url": "https://github.com/D-sorganization/Repository_Management/issues/102",
    "labels": [{"name": "board:proposal"}],
}

MOCK_DECIDED_PROPOSAL = {
    "number": 99,
    "title": "[Proposal] Decided accepted proposal",
    "body": "### Problem Statement\nAlready accepted by the board.",
    "state": "open",
    "created_at": "2026-09-24T10:00:00Z",
    "closed_at": None,
    "html_url": "https://github.com/D-sorganization/Repository_Management/issues/99",
    "labels": [{"name": "board:proposal"}, {"name": "board:accepted"}],
}

MOCK_CLOSED_PROPOSAL = {
    "number": 98,
    "title": "[Proposal] Closed declined proposal",
    "body": "### Problem Statement\nOld closed proposal.",
    "state": "closed",
    "created_at": "2026-09-23T10:00:00Z",
    "closed_at": "2026-09-24T10:00:00Z",
    "html_url": "https://github.com/D-sorganization/Repository_Management/issues/98",
    "labels": [{"name": "board:proposal"}],
}


@pytest.fixture(autouse=True)
def _clean_stores(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_clear()
    reset_conversation_store()
    reset_run_store()
    reset_work_item_store()
    monkeypatch.setattr("projects.service.configured_repos", lambda: [])
    monkeypatch.setattr("agent_remediation.provider_probe.probe_provider_availability", lambda: {})


@pytest.mark.asyncio
async def test_open_proposals_appear_in_inbox_and_are_counted() -> None:
    """(a) open proposals appear as inbox items and are counted."""
    mock_issues = [MOCK_OPEN_PROPOSAL_1, MOCK_OPEN_PROPOSAL_2]

    with patch("proposals.store.list_github_proposals", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_issues
        inbox = await collect_inbox()

    assert inbox.sources["board_proposals"].status == "ok"
    assert inbox.sources["board_proposals"].count == 2
    assert inbox.counts["board_proposals"] == 2

    bp_items = [it for it in inbox.items if it.source == "board_proposal"]
    assert len(bp_items) == 2

    # Verify item 101 mapping
    item_101 = next(it for it in bp_items if it.id == "board_proposal_101")
    assert item_101.title == "[Proposal] Add automated database compaction"
    assert "Database files grow indefinitely" in item_101.summary
    assert item_101.severity == "high"  # Urgency: Urgent -> high
    assert item_101.link == "/staff/fleet-command?section=proposals"
    assert item_101.created_at == "2026-09-25T10:00:00Z"
    assert item_101.metadata.get("kind") == "board_proposal"
    assert item_101.metadata.get("proposal_number") == 101

    # Verify item 102 mapping (Urgency: Emergency -> critical)
    item_102 = next(it for it in bp_items if it.id == "board_proposal_102")
    assert item_102.severity == "critical"


@pytest.mark.asyncio
async def test_decided_and_closed_proposals_are_excluded() -> None:
    """(b) decided or closed proposals are excluded from inbox items."""
    mock_issues = [
        MOCK_OPEN_PROPOSAL_1,
        MOCK_DECIDED_PROPOSAL,
        MOCK_CLOSED_PROPOSAL,
    ]

    with patch("proposals.store.list_github_proposals", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_issues
        inbox = await collect_inbox()

    assert inbox.sources["board_proposals"].status == "ok"
    assert inbox.sources["board_proposals"].count == 1
    assert inbox.counts["board_proposals"] == 1

    bp_items = [it for it in inbox.items if it.source == "board_proposal"]
    assert len(bp_items) == 1
    assert bp_items[0].id == "board_proposal_101"


@pytest.mark.asyncio
async def test_store_exception_yields_unavailable_and_renders_cleanly() -> None:
    """(c) a store exception yields unavailable while the rest of the inbox still renders."""
    with patch("proposals.store.list_github_proposals", new_callable=AsyncMock) as mock_list:
        mock_list.side_effect = ProposalStoreError("GitHub rate limit exceeded")
        inbox = await collect_inbox()

    assert inbox.sources["board_proposals"].status == "unavailable"
    assert inbox.sources["board_proposals"].count == 0
    assert "GitHub rate limit exceeded" in (inbox.sources["board_proposals"].error or "")
    assert inbox.counts["board_proposals"] == 0
    # Other sources should not have crashed
    assert "approvals" in inbox.sources
    assert "needs_input" in inbox.sources


@pytest.mark.asyncio
async def test_inbox_caches_proposals_within_ttl() -> None:
    """(d) repeated calls within TTL reuse cached proposals without duplicate store reads."""
    mock_issues = [MOCK_OPEN_PROPOSAL_1]

    with patch("proposals.store.list_github_proposals", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_issues
        inbox_1 = await collect_inbox()
        inbox_2 = await collect_inbox()

    assert mock_list.call_count == 1
    assert inbox_1.counts["board_proposals"] == 1
    assert inbox_2.counts["board_proposals"] == 1


@pytest.mark.asyncio
async def test_github_sources_disabled_never_call_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAFF_INBOX_GITHUB_SOURCES=0 (hermetic staff e2e harness, #1556) skips both
    GitHub-backed sources entirely, without treating them as failed."""
    monkeypatch.setenv("STAFF_INBOX_GITHUB_SOURCES", "0")
    monkeypatch.setattr("projects.service.configured_repos", lambda: ["SomeRepo"])

    with (
        patch("proposals.store.list_github_proposals", new_callable=AsyncMock) as mock_list,
        patch("projects.service.project_overview", new_callable=AsyncMock) as mock_overview,
    ):
        inbox = await collect_inbox()

    mock_list.assert_not_called()
    mock_overview.assert_not_called()
    assert inbox.sources["board_proposals"] == SourceStatus(status="ok", count=0)
    assert inbox.sources["project_decisions"] == SourceStatus(status="ok", count=0)
    assert inbox.counts["board_proposals"] == 0
    assert inbox.counts["project_decisions"] == 0
