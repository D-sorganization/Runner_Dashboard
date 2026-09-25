"""FleetClient proposals tests (issue #1284, CR-7).

Verifies that:
- The agent submits a proposal via submit_proposal and the issue payload matches the form.
- The agent lists proposals via list_proposals with query parameters.
- Tool invocation via fleet_tools invoke maps arguments correctly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_CLIENTS = Path(__file__).resolve().parents[2] / "clients" / "fleet"
if str(_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_CLIENTS))

from fleet_client import FleetClient  # noqa: E402
from fleet_fixtures import _clean_fleet_env, fake_api  # noqa: E402, F401  # pytest fixtures
from fleet_tools import BY_CLI, BY_TOOL  # noqa: E402


@pytest.fixture
def client(fake_api: Any) -> FleetClient:
    return FleetClient(fake_api.url, token="tok-123", agent="claude", session="claude-1")


@pytest.mark.unit
def test_fleet_client_submit_proposal_payload(client: FleetClient, fake_api: Any) -> None:
    """Agent submits proposal and payload matches all required form fields."""
    res = client.submit_proposal(
        title="Decouple Metrics Storage",
        target_repos=["Runner_Dashboard"],
        problem="Metrics DB file locks cause read timeouts.",
        evidence="Observed 5 sqlite3.OperationalError locks under concurrency.",
        options_considered="1. WAL mode, 2. DuckDB, 3. PostgreSQL",
        lean="Enable WAL mode and bump busy timeout to 5000ms.",
        estimated_cost="High",
        urgency="Urgent",
        code_request_url="https://dashboard.local/feature-requests/55",
    )
    assert res.get("ok") is True

    req = fake_api.last
    assert req.method == "POST"
    assert req.path == "/api/proposals"
    assert req.body is not None
    assert req.body["title"] == "Decouple Metrics Storage"
    assert req.body["target_repos"] == ["Runner_Dashboard"]
    assert req.body["problem"] == "Metrics DB file locks cause read timeouts."
    assert req.body["evidence"] == "Observed 5 sqlite3.OperationalError locks under concurrency."
    assert req.body["options_considered"] == "1. WAL mode, 2. DuckDB, 3. PostgreSQL"
    assert req.body["lean"] == "Enable WAL mode and bump busy timeout to 5000ms."
    assert req.body["estimated_cost"] == "High"
    assert req.body["urgency"] == "Urgent"
    assert req.body["source"] == "claude"
    assert req.body["code_request_url"] == "https://dashboard.local/feature-requests/55"


@pytest.mark.unit
def test_fleet_client_list_proposals(client: FleetClient, fake_api: Any) -> None:
    """Agent lists proposals with state and repo filter query parameters."""
    client.list_proposals(state="decided", repo="Runner_Dashboard")
    req = fake_api.last
    assert req.method == "GET"
    assert req.path == "/api/proposals"
    assert req.query == {"state": "decided", "repo": "Runner_Dashboard"}


@pytest.mark.unit
def test_fleet_tool_submit_proposal_registration(client: FleetClient, fake_api: Any) -> None:
    """submit_proposal tool is registered in BY_TOOL and BY_CLI and invokable."""
    assert "submit_proposal" in BY_TOOL or "submit-proposal" in BY_CLI
    cmd = BY_TOOL.get("submit_proposal") or BY_CLI.get("submit-proposal")
    assert cmd is not None

    args = {
        "title": "Tool Proposal Title",
        "target_repos": ["Runner_Dashboard"],
        "problem": "Problem via tool",
        "evidence": "Evidence via tool",
        "options_considered": "Option A, Option B",
        "lean": "Option A",
        "estimated_cost": "Medium",
        "urgency": "Routine",
    }
    cmd.invoke(client, args)
    req = fake_api.last
    assert req.method == "POST"
    assert req.path == "/api/proposals"
    assert req.body["title"] == "Tool Proposal Title"
