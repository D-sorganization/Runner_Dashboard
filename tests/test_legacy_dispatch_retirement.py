"""SC-G5-6 (issue #1503): legacy dispatch endpoints are unmounted after the 2026-11-23 sunset."""

from __future__ import annotations

from typing import Any


def test_quick_dispatch_removed(make_authed_client: Any, operator_principal: Any) -> None:
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
    assert resp.status_code in (404, 405)


def test_jules_dispatch_removed(make_authed_client: Any, operator_principal: Any) -> None:
    client = make_authed_client(operator_principal)
    resp = client.post(
        "/api/agent-remediation/dispatch-jules",
        headers={"X-Requested-With": "XMLHttpRequest"},
        json={"workflow_file": "Agent-Lease-Reaper.yml", "ref": "main"},
    )
    assert resp.status_code in (404, 405)
