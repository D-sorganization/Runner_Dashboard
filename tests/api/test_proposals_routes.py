"""API tests for Board Proposals (/api/proposals) (issue #1284, CR-7).

Tests cover:
- create (human and bot principals)
- scope denial (403 for missing scope, 401 for unauthenticated)
- required fields enforcement and input sanitisation
- duplicate-candidate response (409 with candidates unless confirm_not_duplicate=True)
- rate limit, counted by the hidden submitter marker rather than the
  caller-controlled `source` field (review #1444 defect 1)
- 503 propagation when GitHub reads fail, for both POST and GET (defect 3)
- decision-label whitelist: board:needs-info is not a decision (defect 4)
- listing with decision labels and consensus link
- proposal detail with Board-Secretary comments

Every fixture issue body is built with ``store.render_proposal_markdown``
(or shaped the same way a real form-filed issue would be) rather than
synthetic top-level ``source``/``target_repos``/``meeting_date`` keys —
GitHub never returns those as top-level issue fields, only inside ``body``
(review #1444 defect 5).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import identity  # noqa: E402
from identity import Principal  # noqa: E402
from proposals import store  # noqa: E402
from proposals.models import CreateProposalRequest  # noqa: E402
from proposals.store import ProposalStoreError  # noqa: E402
from server import app  # noqa: E402

_XHR = {"X-Requested-With": "XMLHttpRequest"}

_TOKENS = {
    "op-token": Principal(id="operator-1", type="human", name="Operator Alice", roles=["operator"]),
    "bot-token": Principal(id="agent-claude", type="bot", name="Claude Agent", roles=["bot"]),
    "viewer-token": Principal(id="viewer-1", type="human", name="Viewer Bob", roles=["viewer"]),
}

_SAMPLE_PROPOSAL_PAYLOAD = {
    "title": "Adopt WebGPU for Client-Side Visualization",
    "target_repos": ["Runner_Dashboard"],
    "problem": "Rendering 10,000+ metrics points in canvas lags the UI.",
    "evidence": "Benchmarks show 12fps with canvas vs 60fps with WebGPU.",
    "options_considered": "Keep canvas, SVG tiling, or a WebGPU pipeline.",
    "lean": "WebGPU pipeline with WebGL fallback.",
    "estimated_cost": "Medium",
    "urgency": "Urgent",
    "code_request_url": "https://dashboard.local/feature-requests/42",
}


def _issue_body(source: str = "operator-1", **overrides: object) -> str:
    """Build a real-shaped issue body from the sample payload via the shared renderer."""
    payload = {**_SAMPLE_PROPOSAL_PAYLOAD, **overrides}
    req = CreateProposalRequest(**payload)
    return store.render_proposal_markdown(req, source)


def _make_issue(
    number: int,
    *,
    state: str = "open",
    labels: list[str] | None = None,
    submitter: str | None = None,
    **overrides: object,
) -> dict:
    """Build a GitHub-issue-shaped dict: no top-level source/target_repos/meeting_date keys."""
    body = _issue_body(**overrides)
    if submitter:
        body = body + f"\n<!-- proposal-submitter: {submitter} -->\n"
    return {
        "number": number,
        "title": overrides.get("title", _SAMPLE_PROPOSAL_PAYLOAD["title"]),
        "html_url": f"https://github.com/D-sorganization/Repository_Management/issues/{number}",
        "state": state,
        "labels": [{"name": lbl} for lbl in (labels or ["board:proposal"])],
        "created_at": "2026-09-25T10:00:00Z",
        "updated_at": "2026-09-25T10:00:00Z",
        "body": body,
    }


@pytest.fixture(autouse=True)
def setup_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda tok: _TOKENS.get(tok))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-peer-secret")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **_XHR}


@pytest.mark.unit
def test_scope_presets_include_proposals_write() -> None:
    """proposals.write must be granted to operator, bot, and loopback."""
    assert "proposals.write" in identity.SCOPE_PRESETS["operator"]
    assert "proposals.write" in identity.SCOPE_PRESETS["bot"]
    assert "proposals.write" not in identity.SCOPE_PRESETS["viewer"]
    assert "proposals.write" in identity.LOOPBACK_SCOPES


@pytest.mark.unit
def test_create_proposal_scope_denial(client: TestClient) -> None:
    """Unauthenticated calls 401; calls without proposals.write 403."""
    resp_unauth = client.post("/api/proposals", json=_SAMPLE_PROPOSAL_PAYLOAD, headers=_XHR)
    assert resp_unauth.status_code == 401

    resp_viewer = client.post(
        "/api/proposals",
        json=_SAMPLE_PROPOSAL_PAYLOAD,
        headers=_auth_header("viewer-token"),
    )
    assert resp_viewer.status_code == 403


@pytest.mark.unit
def test_create_proposal_required_fields(client: TestClient) -> None:
    """Missing or empty required fields fail validation."""
    for field in ("title", "problem", "evidence", "lean", "estimated_cost", "urgency"):
        payload = dict(_SAMPLE_PROPOSAL_PAYLOAD)
        payload[field] = ""
        resp = client.post("/api/proposals", json=payload, headers=_auth_header("op-token"))
        assert resp.status_code == 422, f"Field {field} should have failed validation"

    payload = dict(_SAMPLE_PROPOSAL_PAYLOAD)
    payload["target_repos"] = []
    resp = client.post("/api/proposals", json=payload, headers=_auth_header("op-token"))
    assert resp.status_code == 422


@pytest.mark.unit
def test_create_proposal_rejects_heading_injection_and_bad_repo_name(client: TestClient) -> None:
    """A '#'-heading line or a shell-hostile repo name is rejected at the boundary (defect 6)."""
    payload = dict(_SAMPLE_PROPOSAL_PAYLOAD)
    payload["problem"] = "Fine problem\n## Injected heading"
    resp = client.post("/api/proposals", json=payload, headers=_auth_header("op-token"))
    assert resp.status_code == 422

    payload = dict(_SAMPLE_PROPOSAL_PAYLOAD)
    payload["target_repos"] = ["Runner_Dashboard; rm -rf /"]
    resp = client.post("/api/proposals", json=payload, headers=_auth_header("op-token"))
    assert resp.status_code == 422

    payload = dict(_SAMPLE_PROPOSAL_PAYLOAD)
    payload["code_request_url"] = "javascript:alert(1)"
    resp = client.post("/api/proposals", json=payload, headers=_auth_header("op-token"))
    assert resp.status_code == 422


@pytest.mark.unit
def test_create_proposal_human_success(client: TestClient) -> None:
    """Operator creates proposal; GitHub issue is created with board:proposal and needs-decision labels."""
    # The mocked GitHub response stands in for whatever GitHub actually
    # stored; give it the "human" source the service itself would have
    # rendered (req.source is None, caller.type == "human") so the parsed
    # response reflects a real round trip rather than the fixture default.
    created_issue = _make_issue(101, submitter="operator-1", source="human")

    with (
        patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=[])),
        patch("proposals.store.create_github_proposal", new=AsyncMock(return_value=created_issue)) as mock_create,
    ):
        resp = client.post(
            "/api/proposals",
            json=_SAMPLE_PROPOSAL_PAYLOAD,
            headers=_auth_header("op-token"),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["number"] == 101
        assert data["title"] == _SAMPLE_PROPOSAL_PAYLOAD["title"]
        assert data["source"] == "human"

        mock_create.assert_awaited_once()
        call_kwargs = mock_create.await_args.kwargs
        assert call_kwargs["title"] == _SAMPLE_PROPOSAL_PAYLOAD["title"]
        # The service passes no explicit `labels` kwarg — create_github_proposal
        # itself is responsible for always including both labels (see
        # test_created_issue_carries_needs_decision_label in the store unit
        # tests). Assert the rendered body actually carries the submitter
        # marker instead of a vacuous default-valued .get() (review #1444
        # defect 5).
        assert "<!-- proposal-submitter: operator-1 -->" in call_kwargs["body"]


@pytest.mark.unit
def test_create_proposal_bot_success(client: TestClient) -> None:
    """Bot principal creates proposal; source records agent id."""
    payload = dict(_SAMPLE_PROPOSAL_PAYLOAD)
    payload["title"] = "Optimize Runner Heartbeat Polling"
    created_issue = _make_issue(102, submitter="agent-claude", title=payload["title"], source="agent-claude")

    with (
        patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=[])),
        patch("proposals.store.create_github_proposal", new=AsyncMock(return_value=created_issue)),
    ):
        resp = client.post(
            "/api/proposals",
            json=payload,
            headers=_auth_header("bot-token"),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["number"] == 102
        assert data["source"] == "agent-claude"


@pytest.mark.unit
def test_create_proposal_duplicate_candidate_response(client: TestClient) -> None:
    """Duplicate candidate detection returns 409 unless confirm_not_duplicate is True."""
    existing_issue = _make_issue(90, submitter="operator-1", title="Adopt WebGPU for Fast Visualization", state="open")

    with patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=[existing_issue])):
        resp = client.post(
            "/api/proposals",
            json=_SAMPLE_PROPOSAL_PAYLOAD,
            headers=_auth_header("op-token"),
        )
        assert resp.status_code == 409
        body = resp.json()
        candidates = body.get("candidates") or body.get("detail", {}).get("candidates")
        assert candidates is not None
        assert len(candidates) == 1
        assert candidates[0]["number"] == 90

    created_issue = _make_issue(103, submitter="operator-1")
    payload_confirmed = {**_SAMPLE_PROPOSAL_PAYLOAD, "confirm_not_duplicate": True}
    with (
        patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=[existing_issue])),
        patch("proposals.store.create_github_proposal", new=AsyncMock(return_value=created_issue)),
    ):
        resp = client.post(
            "/api/proposals",
            json=payload_confirmed,
            headers=_auth_header("op-token"),
        )
        assert resp.status_code == 201


@pytest.mark.unit
def test_create_proposal_rate_limit_counted_by_submitter_marker_not_source(client: TestClient) -> None:
    """The rate limit counts the hidden submitter marker, not the caller-controlled `source` field (defect 1).

    A bot cannot dodge its cap by setting `source` to something else: all 5
    open issues below carry a *different* display `source` string each time
    but the same hidden `<!-- proposal-submitter: agent-claude -->` marker,
    and the 5th submission is still blocked.
    """
    open_bot_proposals = [
        _make_issue(i, submitter="agent-claude", source=f"decoy-source-{i}", title=f"Unrelated fleet task {i}")
        for i in range(1, 6)
    ]

    with patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=open_bot_proposals)):
        resp_bot = client.post(
            "/api/proposals",
            json=_SAMPLE_PROPOSAL_PAYLOAD,
            headers=_auth_header("bot-token"),
        )
        assert resp_bot.status_code == 429
        assert "rate limit" in resp_bot.text.lower()

    # Human with 5 open proposals is NOT rate limited.
    created_issue = _make_issue(104, submitter="operator-1")
    with (
        patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=open_bot_proposals)),
        patch("proposals.store.create_github_proposal", new=AsyncMock(return_value=created_issue)),
    ):
        resp_human = client.post(
            "/api/proposals",
            json=_SAMPLE_PROPOSAL_PAYLOAD,
            headers=_auth_header("op-token"),
        )
        assert resp_human.status_code == 201


@pytest.mark.unit
def test_create_proposal_rate_limit_boundary_allows_fourth_open(client: TestClient) -> None:
    """4 open proposals under the submitter marker is allowed; the 5th is blocked (defect 1 boundary)."""
    four_open = [_make_issue(i, submitter="agent-claude", title=f"Unrelated fleet task {i}") for i in range(1, 5)]
    created_issue = _make_issue(105, submitter="agent-claude")

    with (
        patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=four_open)),
        patch("proposals.store.create_github_proposal", new=AsyncMock(return_value=created_issue)),
    ):
        resp = client.post(
            "/api/proposals",
            json=_SAMPLE_PROPOSAL_PAYLOAD,
            headers=_auth_header("bot-token"),
        )
        assert resp.status_code == 201


@pytest.mark.unit
def test_create_proposal_returns_503_when_github_read_fails(client: TestClient) -> None:
    """A GitHub read failure on the pre-create open-issues fetch surfaces as 503, never as an empty list (defect 3)."""
    with patch(
        "proposals.store.list_github_proposals",
        new=AsyncMock(side_effect=ProposalStoreError("GitHub API error (502): bad gateway")),
    ):
        resp = client.post(
            "/api/proposals",
            json=_SAMPLE_PROPOSAL_PAYLOAD,
            headers=_auth_header("op-token"),
        )
        assert resp.status_code == 503


@pytest.mark.unit
def test_list_proposals_returns_503_when_github_read_fails(client: TestClient) -> None:
    """GET /api/proposals surfaces a GitHub read failure as 503, not as an empty listing (defect 3)."""
    with patch(
        "proposals.store.list_github_proposals",
        new=AsyncMock(side_effect=ProposalStoreError("GitHub API error (502): bad gateway")),
    ):
        resp = client.get("/api/proposals", headers=_auth_header("op-token"))
        assert resp.status_code == 503


@pytest.mark.unit
def test_get_proposal_returns_503_when_comments_fetch_fails(client: TestClient) -> None:
    """GET /api/proposals/{number} surfaces a comments read failure as 503 (defect 3)."""
    issue = _make_issue(85, submitter="operator-1")
    with (
        patch("proposals.store.get_github_proposal", new=AsyncMock(return_value=issue)),
        patch(
            "proposals.store.get_github_proposal_comments",
            new=AsyncMock(side_effect=ProposalStoreError("GitHub API error (502): bad gateway")),
        ),
    ):
        resp = client.get("/api/proposals/85", headers=_auth_header("op-token"))
        assert resp.status_code == 503


@pytest.mark.unit
def test_needs_info_label_is_not_a_decision(client: TestClient) -> None:
    """board:needs-info is secretary bookkeeping, not a decision outcome (defect 4)."""
    issue = _make_issue(
        91, submitter="operator-1", state="open", labels=["board:proposal", "needs-decision", "board:needs-info"]
    )
    with patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=[issue])):
        resp = client.get("/api/proposals?state=open", headers=_auth_header("op-token"))
        assert resp.status_code == 200
        items = resp.json()["proposals"]
        assert len(items) == 1
        assert items[0]["decision"] is None
        assert "board:needs-info" not in items[0]["decision_labels"]

        resp_decided = client.get("/api/proposals?state=decided", headers=_auth_header("op-token"))
        assert resp_decided.status_code == 200
        assert resp_decided.json()["proposals"] == []


@pytest.mark.unit
def test_list_proposals_with_decision_labels_and_consensus_link(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/proposals lists proposals with decision labels and a consensus.md link (defect 8)."""
    rm_root = tmp_path / "Repository_Management"
    # staff.workspace.rm_root() requires this marker file to trust the
    # checkout, matching the pattern used by backend/priorities/service.py's
    # own tests — proposals/service.py now reuses the same helper (DRY,
    # review #1444 defect 7) instead of a bespoke resolver.
    scripts_dir = rm_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "check_agent_claim.py").write_text("", encoding="utf-8")

    meeting_dir = rm_root / "docs" / "board-meetings" / "2026-09-21"
    meeting_dir.mkdir(parents=True)
    consensus_file = meeting_dir / "consensus.md"
    consensus_file.write_text(
        "# Board Consensus Template\n## Meeting Metadata\n| Field | Value |\n| **Meeting Date** | `2026-09-21` |\n"
        "## Consensus Priority List\n### Active Priorities (Execute This Cycle)\n"
        "1. **`Adopt WebGPU`** — `Runner_Dashboard` — `fast canvas`\n   - Epic/Issue: `#77`\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm_root))

    issues = [
        _make_issue(
            77,
            submitter="operator-1",
            state="closed",
            labels=["board:proposal", "board:accepted"],
            title="Adopt WebGPU for Fast Visualization",
        ),
        _make_issue(78, submitter="operator-1", state="open", title="Add Dark Mode Option"),
    ]

    with patch("proposals.store.list_github_proposals", new=AsyncMock(return_value=issues)):
        resp = client.get("/api/proposals", headers=_auth_header("op-token"))
        assert resp.status_code == 200
        items = resp.json().get("proposals", [])
        assert len(items) == 2

        resp_decided = client.get("/api/proposals?state=decided", headers=_auth_header("op-token"))
        assert resp_decided.status_code == 200
        decided_items = resp_decided.json().get("proposals", [])
        assert len(decided_items) == 1
        item = decided_items[0]
        assert item["number"] == 77
        assert "board:accepted" in item["decision_labels"]
        assert item["decision"] == "accepted"
        assert item["meeting_date"] == "2026-09-21"
        assert item["consensus_url"] == (
            "https://github.com/D-sorganization/Repository_Management/blob/main/docs/board-meetings/2026-09-21/consensus.md"
        )

        resp_open = client.get("/api/proposals?state=open", headers=_auth_header("op-token"))
        assert resp_open.status_code == 200
        open_items = resp_open.json().get("proposals", [])
        assert len(open_items) == 1
        assert open_items[0]["number"] == 78


@pytest.mark.unit
def test_get_proposal_detail_with_secretary_comments(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/proposals/{number} returns detail and secretary comments, using the configured login list (defect 9)."""
    monkeypatch.setenv("BOARD_SECRETARY_LOGINS", "board-secretary")
    issue = _make_issue(85, submitter="operator-1", title="Proposal with Secretary feedback")
    comments = [
        {
            "id": 1001,
            "user": {"login": "Board-Secretary"},
            "body": "Scheduled for Board review at meeting 2026-09-28.",
            "created_at": "2026-09-21T12:00:00Z",
        },
        {
            "id": 1002,
            # Would have false-matched the old substring check ("board" in
            # login), but is not on the configured secretary list.
            "user": {"login": "board-observer"},
            "body": "Following along.",
            "created_at": "2026-09-21T13:00:00Z",
        },
    ]

    with (
        patch("proposals.store.get_github_proposal", new=AsyncMock(return_value=issue)),
        patch("proposals.store.get_github_proposal_comments", new=AsyncMock(return_value=comments)),
    ):
        resp = client.get("/api/proposals/85", headers=_auth_header("op-token"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["number"] == 85
        assert len(data["comments"]) == 2
        assert data["comments"][0]["user"]["login"] == "Board-Secretary"
        assert data["comments"][0]["is_secretary"] is True
        assert data["comments"][1]["is_secretary"] is False
