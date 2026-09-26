"""Tests for SC-G5-6 (issue #1503): Retirement of legacy dispatch endpoints.

Verifies that:
- POST /api/agents/quick-dispatch returns HTTP 410 Gone with Link pointing to /api/v1/staff/requests.
- POST /api/agent-remediation/dispatch-jules returns HTTP 410 Gone with Link pointing to /api/v1/staff/requests.
"""

from __future__ import annotations

from typing import Any


def test_quick_dispatch_returns_410_gone(make_authed_client: Any, operator_principal: Any) -> None:
    """POST /api/agents/quick-dispatch must return HTTP 410 Gone with Link header pointing to /api/v1/staff/requests."""
    client = make_authed_client(operator_principal)
    resp = client.post(
        "/api/agents/quick-dispatch",
        headers={"X-Requested-With": "XMLHttpRequest"},
        json={
            "repository": "Runner_Dashboard",
            "prompt": "Investigate failing test",
            "provider": "claude_code_cli",
        },
    )
    assert resp.status_code == 410

    link_header = resp.headers.get("Link")
    assert link_header is not None
    assert "</api/v1/staff/requests>" in link_header
    assert 'rel="successor-version"' in link_header
    assert "Sunset" in resp.headers

    body = resp.json()
    detail = body.get("detail", "") or body.get("error", "")
    assert "retired" in detail.lower()
    assert "/api/v1/staff/requests" in detail or body.get("redirect_url") == "/api/v1/staff/requests"


def test_jules_dispatch_returns_410_gone(make_authed_client: Any, operator_principal: Any) -> None:
    """POST /api/agent-remediation/dispatch-jules must return HTTP 410 Gone with Link header."""
    client = make_authed_client(operator_principal)
    resp = client.post(
        "/api/agent-remediation/dispatch-jules",
        headers={"X-Requested-With": "XMLHttpRequest"},
        json={"workflow_file": "Agent-Lease-Reaper.yml", "ref": "main"},
    )
    assert resp.status_code == 410

    link_header = resp.headers.get("Link")
    assert link_header is not None
    assert "</api/v1/staff/requests>" in link_header
    assert 'rel="successor-version"' in link_header
    assert "Sunset" in resp.headers

    body = resp.json()
    detail = body.get("detail", "") or body.get("error", "")
    assert "retired" in detail.lower()
    assert "/api/v1/staff/requests" in detail or body.get("redirect_url") == "/api/v1/staff/requests"
