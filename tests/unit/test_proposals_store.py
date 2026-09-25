"""Unit tests for Board Proposals markdown rendering/parsing and decision labels.

Covers review defects filed against PR #1444 (issue #1284):
- defect 2: render/parse must match the real board-proposal issue form
  headings and dropdown enums exactly, including a body shaped like one a
  human filed directly through the GitHub form.
- defect 4: only accepted/declined/deferred count as a decision; needs-info
  is not a decision and there is no board:decided label.
- defect 6: input sanitisation rejects markdown heading injection, invalid
  repo names, and non-https code_request_url values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from proposals.models import CreateProposalRequest  # noqa: E402
from proposals.store import (  # noqa: E402
    NEEDS_DECISION_LABEL,
    PROPOSAL_LABEL,
    extract_decision_info,
    parse_proposal_markdown,
    render_proposal_markdown,
)

_VALID_PAYLOAD = {
    "title": "Adopt WebGPU for Visualization",
    "target_repos": ["Runner_Dashboard"],
    "problem": "Rendering lags the UI.",
    "evidence": "Benchmarks show 12fps.",
    "options_considered": "Keep canvas, or WebGPU.",
    "lean": "WebGPU pipeline.",
    "estimated_cost": "Medium",
    "urgency": "Urgent",
}


@pytest.mark.unit
def test_render_parse_round_trip() -> None:
    """render_proposal_markdown -> parse_proposal_markdown recovers every field (defect 2)."""
    req = CreateProposalRequest(**_VALID_PAYLOAD)
    body = render_proposal_markdown(req, source="claude-session-1")
    parsed = parse_proposal_markdown(body)

    assert parsed["target_repos"] == ["Runner_Dashboard"]
    assert parsed["problem"] == "Rendering lags the UI."
    assert parsed["evidence"] == "Benchmarks show 12fps."
    assert parsed["options_considered"] == "Keep canvas, or WebGPU."
    assert parsed["lean"] == "WebGPU pipeline."
    assert parsed["estimated_cost"] == "Medium"
    assert parsed["urgency"] == "Urgent"
    assert parsed["source"] == "claude-session-1"
    assert parsed["code_request_url"] is None


@pytest.mark.unit
def test_render_headings_match_real_board_proposal_form() -> None:
    """Rendered headings must match board-proposal.yml's labels exactly (defect 2)."""
    req = CreateProposalRequest(**_VALID_PAYLOAD)
    body = render_proposal_markdown(req, source="dieterolson")

    assert "### Submitter / Source" in body
    assert "### Target Repository / Repositories" in body
    assert "### Problem Statement" in body
    assert "### Evidence and Context" in body
    assert "### Options Considered" in body
    assert "### Submitter's Lean" in body
    assert "### Estimated Effort" in body
    assert "### Urgency" in body
    assert "### Linked Code Request or Issue (Optional)" in body


@pytest.mark.unit
def test_parse_body_shaped_like_a_real_form_filed_issue() -> None:
    """A human filing the GitHub form directly renders '_No response_' for a blank optional field."""
    body = """### Submitter / Source
dieterolson

### Target Repository / Repositories
Repository_Management, Runner_Dashboard

### Problem Statement
The dispatcher has no fallback when the primary runner pool is saturated.

### Evidence and Context
See Tools_Private#1797 for the 39-PR queue backup.

### Options Considered
### Option A: Add a secondary pool
- **Pros**: simple
- **Cons**: cost

### Submitter's Lean
Option A.

### Estimated Effort
High

### Urgency
Urgent (within 48 hours)

### Linked Code Request or Issue (Optional)

_No response_
"""
    parsed = parse_proposal_markdown(body)

    assert parsed["target_repos"] == ["Repository_Management", "Runner_Dashboard"]
    assert parsed["source"] == "dieterolson"
    assert "Tools_Private#1797" in parsed["evidence"]
    assert parsed["estimated_cost"] == "High"
    assert parsed["code_request_url"] is None


@pytest.mark.unit
def test_decision_labels_whitelist_excludes_needs_info_and_decided() -> None:
    """board:needs-info is not a decision; board:decided does not exist (defect 4)."""
    issue = {
        "state": "open",
        "labels": [
            {"name": PROPOSAL_LABEL},
            {"name": NEEDS_DECISION_LABEL},
            {"name": "board:needs-info"},
        ],
    }
    decision, decision_labels = extract_decision_info(issue)
    assert decision is None
    assert decision_labels == []


@pytest.mark.unit
def test_decision_labels_accept_only_known_outcomes() -> None:
    """accepted/declined/deferred are recognised; a fictional board:decided label is not (defect 4)."""
    issue = {
        "state": "closed",
        "labels": [{"name": PROPOSAL_LABEL}, {"name": "board:accepted"}, {"name": "board:decided"}],
    }
    decision, decision_labels = extract_decision_info(issue)
    assert decision == "accepted"
    assert decision_labels == ["board:accepted"]


@pytest.mark.unit
def test_created_issue_carries_needs_decision_label() -> None:
    """Every created proposal issue is labelled board:proposal and needs-decision, matching the form (defect 2)."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from proposals import store

    with patch("proposals.store.gh_api_write", new=AsyncMock(return_value={"number": 1})) as mock_post:
        asyncio.run(store.create_github_proposal(title="t", body="b"))
        labels = mock_post.await_args.args[2]["labels"]
        assert PROPOSAL_LABEL in labels
        assert NEEDS_DECISION_LABEL in labels


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Fine title\n### Injected heading"),
        ("problem", "Fine problem\n## Injected heading"),
    ],
)
def test_heading_injection_rejected(field: str, value: str) -> None:
    """A '#'-prefixed line in free text is rejected, not silently folded into a fake section (defect 6)."""
    payload = dict(_VALID_PAYLOAD)
    payload[field] = value
    with pytest.raises(Exception, match="heading"):
        CreateProposalRequest(**payload)


@pytest.mark.unit
def test_invalid_repo_name_rejected() -> None:
    """target_repos entries are validated against a repo-name pattern (defect 6)."""
    payload = dict(_VALID_PAYLOAD)
    payload["target_repos"] = ["Runner_Dashboard; rm -rf /"]
    with pytest.raises(Exception, match="not a valid repo name"):
        CreateProposalRequest(**payload)


@pytest.mark.unit
def test_code_request_url_must_be_https() -> None:
    """code_request_url must be an https URL (defect 6)."""
    payload = dict(_VALID_PAYLOAD)
    payload["code_request_url"] = "javascript:alert(1)"
    with pytest.raises(Exception, match="https URL"):
        CreateProposalRequest(**payload)


@pytest.mark.unit
def test_estimated_effort_and_urgency_reject_free_text() -> None:
    """estimated_cost/urgency must be one of the form's exact dropdown values (defect 2)."""
    payload = dict(_VALID_PAYLOAD)
    payload["estimated_cost"] = "2 sprints"
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        CreateProposalRequest(**payload)

    payload = dict(_VALID_PAYLOAD)
    payload["urgency"] = "high"
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        CreateProposalRequest(**payload)
