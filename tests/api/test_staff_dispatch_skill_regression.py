"""Regression: POST /api/staff/{role}/run must not 422 on skill registry gaps (#2157)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytest_plugins = ["tests.api.test_staff_fleet"]

_XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.mark.unit
def test_ad_hoc_dry_run_runner_dashboard(client: TestClient) -> None:
    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={
            "repo": "Runner_Dashboard",
            "prompt": "ping",
            "dry_run": True,
            "task_type": "staff-dispatch",
        },
        headers=_XHR,
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert body["dry_run"] is True
    plan = body["plan"]
    assert plan["role"] == "ad-hoc"
    assert plan["repo"] == "Runner_Dashboard"
    assert plan.get("skill_pin", "").startswith("fleet-staff-unattended@")
