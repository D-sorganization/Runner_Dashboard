"""Route validation for staff cloud session dispatch (#2627)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.api.test_agent_grok_fleet_read import (
    _XHR,
    _auth,
)
from tests.api.test_agent_grok_fleet_read import (
    grok_env as _grok_env_fixture,  # noqa: F401
)
from tests.api.test_agent_grok_fleet_read import (
    remote_client as _remote_client_fixture,  # noqa: F401
)


@pytest.mark.integration
def test_cloud_capabilities_route(remote_client: TestClient, grok_env: dict[str, str], staff_cli_installed) -> None:
    staff_cli_installed("claude")
    resp = remote_client.get("/api/v1/staff/cloud-capabilities", headers=_auth(grok_env["grok"]))
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "claude" in data["providers"]


@pytest.mark.integration
def test_dispatch_dry_run_cloud_argv(remote_client: TestClient, grok_env: dict[str, str], staff_cli_installed) -> None:
    staff_cli_installed("claude")
    resp = remote_client.post(
        "/api/staff/ad-hoc/run",
        json={
            "prompt": "cloud task",
            "provider": "claude",
            "dry_run": True,
            "execution_mode": "cloud",
        },
        headers={**_XHR, **_auth(grok_env["grok"])},
    )
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    assert plan["argv"][:2] == ["claude", "--remote"]
    assert plan.get("execution_mode") == "cloud"
