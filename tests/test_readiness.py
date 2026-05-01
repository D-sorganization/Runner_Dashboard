"""Tests for backend/readiness.py and /livez + /readyz endpoints — issue #332.

Verifies:
- /livez always returns 200 regardless of dependency state
- /readyz returns 503 when a probe is down
- /readyz returns 200 when all probes pass
- aggregate() computes correct overall status
- Individual probe classes behave correctly
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import readiness as r  # noqa: E402

# ---------------------------------------------------------------------------
# Unit tests for individual probes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gh_token_probe_ok(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "fake-token-abc")
    probe = r.GhTokenProbe()
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_gh_token_probe_down(monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    probe = r.GhTokenProbe()
    status, detail = await probe.check()
    assert status == "down"
    assert detail is not None


@pytest.mark.asyncio
async def test_gh_cli_probe_ok(monkeypatch) -> None:
    with patch("readiness.shutil.which", return_value="/usr/bin/gh"):
        probe = r.GhCliProbe()
        status, detail = await probe.check()
    assert status == "ok"


@pytest.mark.asyncio
async def test_gh_cli_probe_down(monkeypatch) -> None:
    with patch("readiness.shutil.which", return_value=None):
        probe = r.GhCliProbe()
        status, detail = await probe.check()
    assert status == "down"
    assert "gh" in (detail or "")


# ---------------------------------------------------------------------------
# aggregate() logic
# ---------------------------------------------------------------------------


class _OkProbe:
    name = "ok_probe"

    async def check(self) -> tuple[r.ProbeStatus, str | None]:
        return "ok", None


class _DownProbe:
    name = "down_probe"

    async def check(self) -> tuple[r.ProbeStatus, str | None]:
        return "down", "simulated failure"


class _DegradedProbe:
    name = "degraded_probe"

    async def check(self) -> tuple[r.ProbeStatus, str | None]:
        return "degraded", "something is slow"


@pytest.mark.asyncio
async def test_aggregate_all_ok() -> None:
    status, body = await r.aggregate([_OkProbe(), _OkProbe()])
    assert status == 200
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_aggregate_any_down() -> None:
    status, body = await r.aggregate([_OkProbe(), _DownProbe()])
    assert status == 503
    assert body["status"] == "down"


@pytest.mark.asyncio
async def test_aggregate_degraded_no_down() -> None:
    status, body = await r.aggregate([_OkProbe(), _DegradedProbe()])
    assert status == 503
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_aggregate_checks_payload_structure() -> None:
    status, body = await r.aggregate([_OkProbe(), _DownProbe()])
    assert "checks" in body
    assert "ok_probe" in body["checks"]
    assert "down_probe" in body["checks"]
    # Down probe should have detail
    assert isinstance(body["checks"]["down_probe"], dict)
    assert body["checks"]["down_probe"]["status"] == "down"


# ---------------------------------------------------------------------------
# /livez endpoint — always 200
# ---------------------------------------------------------------------------


def test_livez_always_returns_200() -> None:
    import health as h
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(h.router)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_livez_route_exists_in_health_router() -> None:
    import health as h

    paths = {r.path for r in h.router.routes}  # type: ignore[attr-defined]
    assert "/livez" in paths, "health router must expose /livez"


# ---------------------------------------------------------------------------
# /readyz endpoint — 503 when probe fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readyz_503_when_gh_token_missing(monkeypatch) -> None:
    """With GH_TOKEN absent, /readyz must return 503."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with patch("readiness.shutil.which", return_value="/usr/bin/gh"):
        status, body = await r.aggregate([r.GhTokenProbe(), r.GhCliProbe()])
    assert status == 503
    assert body["status"] == "down"
    assert "github_token" in body["checks"]


@pytest.mark.asyncio
async def test_readyz_503_when_gh_cli_absent(monkeypatch) -> None:
    """With gh CLI absent, /readyz must return 503."""
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    with patch("readiness.shutil.which", return_value=None):
        status, body = await r.aggregate([r.GhTokenProbe(), r.GhCliProbe()])
    assert status == 503


# ---------------------------------------------------------------------------
# health.py structural assertions
# ---------------------------------------------------------------------------


def test_health_module_has_livez_route() -> None:
    import health as h

    paths = {r.path for r in h.router.routes}  # type: ignore[attr-defined]
    assert "/livez" in paths, "health.py must expose /livez"


def test_health_module_has_readyz_route() -> None:
    import health as h

    paths = {r.path for r in h.router.routes}  # type: ignore[attr-defined]
    assert "/readyz" in paths, "health.py must expose /readyz"


def test_health_module_no_import_from_server_at_module_level() -> None:
    """health.py must not import from server at module level (would cause circular imports)."""
    health_src = (_BACKEND_DIR / "health.py").read_text(encoding="utf-8")
    # Module-level 'from server import' or 'import server' are forbidden.
    lines = [
        line
        for line in health_src.splitlines()
        if not line.strip().startswith("#") and not line.strip().startswith('"""')
    ]
    for line in lines:
        stripped = line.strip()
        # Allow lazy imports inside function bodies (indented)
        if line.startswith("    ") or line.startswith("\t"):
            continue
        assert "from server import" not in stripped, f"health.py must not import from server at module level: {line!r}"
        assert stripped != "import server", "health.py must not import server at module level"
