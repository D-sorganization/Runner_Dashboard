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
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setenv("GH_TOKEN", "fake-token-abc")
    probe = r.GhTokenProbe()
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_gh_token_probe_ok_with_github_app(monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_FILE", "/run/secrets/github-app.pem")

    probe = r.GhTokenProbe()
    status, detail = await probe.check()

    assert status == "ok"
    assert detail == "GitHub App auth configured"


@pytest.mark.asyncio
async def test_gh_token_probe_down(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
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
    """With all GitHub credentials absent, /readyz must return 503."""
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_FILE", raising=False)
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


# ---------------------------------------------------------------------------
# RunnerHealthProbe — A6 (surface failed local-runner units in /readyz)
# ---------------------------------------------------------------------------


def _systemctl_json(units: list[dict]) -> bytes:
    """Encode a fake `systemctl list-units --output=json` payload."""
    import json as _json

    return _json.dumps(units).encode("utf-8")


class _FakeProc:
    """Minimal asyncio.create_subprocess_exec result for tests."""

    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


@pytest.mark.asyncio
async def test_runner_health_probe_ok_when_no_failed_units(monkeypatch) -> None:
    """A6: probe reports ok when no actions.runner.* units are in 'failed' state."""
    units = [
        {"unit": "actions.runner.org.runner-1.service", "active": "active", "sub": "running"},
        {"unit": "actions.runner.org.runner-2.service", "active": "active", "sub": "running"},
    ]

    async def fake_exec(*_args, **_kwargs):
        return _FakeProc(_systemctl_json(units))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    probe = r.RunnerHealthProbe(cache_ttl_seconds=0)  # disable cache for tests
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_runner_health_probe_degraded_when_one_failed(monkeypatch) -> None:
    """A6: 1 failed out of 10 (10% boundary) is degraded, not critical."""
    units = [
        {"unit": f"actions.runner.org.runner-{i}.service", "active": "active", "sub": "running"} for i in range(9)
    ] + [{"unit": "actions.runner.org.runner-10.service", "active": "failed", "sub": "failed"}]

    async def fake_exec(*_args, **_kwargs):
        return _FakeProc(_systemctl_json(units))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    probe = r.RunnerHealthProbe(cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "degraded"
    assert detail is not None and "actions.runner.org.runner-10.service" in detail


@pytest.mark.asyncio
async def test_runner_health_probe_down_when_majority_failed(monkeypatch) -> None:
    """A6: >10% failed means the fleet is critically degraded — return down."""
    units = [
        {"unit": "actions.runner.org.runner-1.service", "active": "failed", "sub": "failed"},
        {"unit": "actions.runner.org.runner-2.service", "active": "failed", "sub": "failed"},
        {"unit": "actions.runner.org.runner-3.service", "active": "failed", "sub": "failed"},
        {"unit": "actions.runner.org.runner-4.service", "active": "active", "sub": "running"},
        {"unit": "actions.runner.org.runner-5.service", "active": "active", "sub": "running"},
    ]

    async def fake_exec(*_args, **_kwargs):
        return _FakeProc(_systemctl_json(units))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    probe = r.RunnerHealthProbe(cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "down"
    assert detail is not None


@pytest.mark.asyncio
async def test_runner_health_probe_ok_when_no_units_registered(monkeypatch) -> None:
    """A6: a machine with no runners (e.g. dashboard-only node) is not degraded."""

    async def fake_exec(*_args, **_kwargs):
        return _FakeProc(_systemctl_json([]))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    probe = r.RunnerHealthProbe(cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "ok"


@pytest.mark.asyncio
async def test_runner_health_probe_degraded_when_systemctl_unavailable(monkeypatch) -> None:
    """A6: systemctl missing (e.g. WSL host, dev box) must not crash readiness — degrade gracefully."""

    async def fake_exec(*_args, **_kwargs):
        raise FileNotFoundError("systemctl not found")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    probe = r.RunnerHealthProbe(cache_ttl_seconds=0)
    status, detail = await probe.check()
    # Not having systemctl is not a hard failure of the *dashboard* — it just
    # means we can't observe local runners. /readyz remains 200; ops sees
    # the degraded note in the body.
    assert status == "degraded"
    assert detail is not None and "systemctl" in detail.lower()


@pytest.mark.asyncio
async def test_runner_health_probe_degraded_on_subprocess_timeout(monkeypatch) -> None:
    """A6: hanging systemctl must time out within bounded seconds (no /readyz hang)."""

    async def fake_exec(*_args, **_kwargs):
        class _SlowProc:
            returncode = 0

            async def communicate(self):
                import asyncio as _a

                await _a.sleep(60)  # would block far past the probe timeout
                return b"[]", b""

        return _SlowProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    # Use a tiny timeout to make the test fast.
    probe = r.RunnerHealthProbe(cache_ttl_seconds=0, subprocess_timeout_seconds=0.05)
    status, detail = await probe.check()
    assert status == "degraded"
    assert detail is not None and "timeout" in detail.lower()


@pytest.mark.asyncio
async def test_runner_health_probe_caches_result(monkeypatch) -> None:
    """A6: rapid /readyz polls must not fork-bomb systemctl — observe TTL cache."""
    call_count = {"n": 0}

    async def fake_exec(*_args, **_kwargs):
        call_count["n"] += 1
        return _FakeProc(_systemctl_json([]))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    probe = r.RunnerHealthProbe(cache_ttl_seconds=60)
    await probe.check()
    await probe.check()
    await probe.check()
    assert call_count["n"] == 1, "expected the second and third checks to be served from cache"


def test_runner_health_probe_is_in_default_probes() -> None:
    """A6: the probe must be registered with the default readiness probe list,
    otherwise /readyz won't surface failed runners."""
    names = {p.name for p in r.get_default_probes()}
    assert "runner_health" in names
