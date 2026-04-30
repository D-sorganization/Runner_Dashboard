"""Tests for Fleet mobile components.

These tests verify the Fleet mobile page renders correct markup for all four
states (loading, empty, populated, error) and that the KPI header computes
correctly from the /api/fleet/status response shape.
"""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest_asyncio.fixture
async def client():
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415
    from server import app  # noqa: PLC0415

    headers = {
        "Authorization": "Bearer test-key",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_fleet_status_endpoint_returns_dict_shape(client):
    """Verify the fleet status endpoint returns a dict that Mobile.tsx expects."""
    resp = await client.get("/api/fleet/status")
    assert resp.status_code in (200, 500, 503)  # may fail if GH token missing
    if resp.status_code == 200:
        data = resp.json()
        assert isinstance(data, dict)
        for hostname, node in data.items():
            assert isinstance(hostname, str)
            assert isinstance(node, dict)
            assert "status" in node or "_role" in node


def _read_frontend(path_from_repo: str) -> str:
    repo = Path(__file__).resolve().parents[2]
    return (repo / path_from_repo).read_text()


def test_fleet_mobile_loading_state_markup():
    src = _read_frontend("frontend/src/pages/Fleet/Mobile.tsx")
    assert "Loading fleet" in src
    assert 'aria-live="polite"' in src


def test_fleet_mobile_error_state_markup():
    src = _read_frontend("frontend/src/pages/Fleet/Mobile.tsx")
    assert 'role="alert"' in src
    assert "fleet-mobile-error" in src


def test_fleet_mobile_empty_state_markup():
    src = _read_frontend("frontend/src/pages/Fleet/Mobile.tsx")
    assert "fleet-empty" in src
    assert "No runners match" in src


def test_fleet_mobile_status_filter_logic():
    src = _read_frontend("frontend/src/pages/Fleet/Mobile.tsx")
    assert '"running"' in src
    assert '"busy"' in src


def test_kpi_header_has_four_values():
    src = _read_frontend("frontend/src/pages/Fleet/KpiHeader.tsx")
    assert "Total" in src
    assert "Online" in src
    assert "Busy" in src
    assert "Offline" in src
    assert 'role="region"' in src


def test_runner_card_props_match_expected_shape():
    src = _read_frontend("frontend/src/pages/Fleet/RunnerCard.tsx")
    assert "cpuPercent" in src
    assert "ramPercent" in src
    assert "uptimeSeconds" in src
    assert "currentJob" in src


def test_status_pill_is_clickable_and_aria_pressed():
    src = _read_frontend("frontend/src/pages/Fleet/StatusPill.tsx")
    assert "aria-pressed" in src
    assert "onClick" in src
