"""Tests for /api/autoscaler/pools endpoint (issue #755).

All systemd / psutil interactions are mocked; nothing touches real hardware.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure backend is on the import path
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------------------
# Unit tests for the router helpers (no HTTP layer)
# ---------------------------------------------------------------------------


def test_pool_scaling_state_model() -> None:
    """PoolScalingState pydantic model accepts valid fields."""
    from routers.autoscaler_pools import PoolScalingState

    state = PoolScalingState(
        pool="nvme",
        min_online=1,
        max_online=16,
        default_online=4,
        pattern="nvme",
        labels=["nvme", "fast-storage"],
        start_enabled=True,
        stop_enabled=True,
        pressure_metric="cache_pressure / nvme_disk_pct",
        cooldown_secs=180,
    )
    assert state.pool == "nvme"
    assert state.min_online == 1
    assert "nvme" in state.labels


def test_pool_config_patch_model_valid() -> None:
    """PoolConfigPatch accepts min <= max."""
    from routers.autoscaler_pools import PoolConfigPatch

    p = PoolConfigPatch(min_online=2, max_online=8)
    assert p.min_online == 2
    assert p.max_online == 8


def test_pool_config_patch_model_rejects_extra_fields() -> None:
    """PoolConfigPatch rejects unknown fields (extra='forbid')."""
    from pydantic import ValidationError
    from routers.autoscaler_pools import PoolConfigPatch

    with pytest.raises(ValidationError):
        PoolConfigPatch(min_online=2, max_online=8, unknown_field="bad")  # type: ignore[call-arg]


def test_pool_config_patch_model_negative_min_rejected() -> None:
    """PoolConfigPatch rejects min_online < 0."""
    from pydantic import ValidationError
    from routers.autoscaler_pools import PoolConfigPatch

    with pytest.raises(ValidationError):
        PoolConfigPatch(min_online=-1, max_online=8)


def test_load_pool_state_returns_three_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    """_load_pool_state returns exactly nvme, hdd, default entries."""
    import autoscaler_config as cfg
    from routers.autoscaler_pools import _load_pool_state

    monkeypatch.setattr(cfg, "NVME_PATTERN", "nvme")
    monkeypatch.setattr(cfg, "HDD_PATTERN", "hdd")
    monkeypatch.setattr(cfg, "COOLDOWN_SECS", 180)

    states = _load_pool_state()
    pool_names = [s.pool for s in states]
    assert pool_names == ["nvme", "hdd", "default"]


def test_load_pool_state_nvme_pressure_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    """NVMe pool uses cache-pressure metric label."""
    import autoscaler_config as cfg
    from routers.autoscaler_pools import _load_pool_state

    monkeypatch.setattr(cfg, "NVME_PATTERN", "nvme")
    monkeypatch.setattr(cfg, "HDD_PATTERN", "hdd")
    monkeypatch.setattr(cfg, "COOLDOWN_SECS", 180)

    states = _load_pool_state()
    nvme = next(s for s in states if s.pool == "nvme")
    assert "cache_pressure" in nvme.pressure_metric


def test_load_pool_state_hdd_pressure_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    """HDD pool uses io-utilization metric label."""
    import autoscaler_config as cfg
    from routers.autoscaler_pools import _load_pool_state

    monkeypatch.setattr(cfg, "NVME_PATTERN", "nvme")
    monkeypatch.setattr(cfg, "HDD_PATTERN", "hdd")
    monkeypatch.setattr(cfg, "COOLDOWN_SECS", 180)

    states = _load_pool_state()
    hdd = next(s for s in states if s.pool == "hdd")
    assert "io" in hdd.pressure_metric.lower()


def test_load_pool_state_respects_config_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """_load_pool_state picks up current config values."""
    import autoscaler_config as cfg
    import runner_autoscaler as ra
    from routers.autoscaler_pools import _load_pool_state

    monkeypatch.setattr(ra, "NVME_MIN_ONLINE", 3)
    monkeypatch.setattr(ra, "NVME_MAX_ONLINE", 12)
    monkeypatch.setattr(ra, "NVME_DEFAULT", 5)
    monkeypatch.setattr(cfg, "NVME_PATTERN", "nvme")
    monkeypatch.setattr(cfg, "HDD_PATTERN", "hdd")
    monkeypatch.setattr(cfg, "COOLDOWN_SECS", 60)

    states = _load_pool_state()
    nvme = next(s for s in states if s.pool == "nvme")
    assert nvme.min_online == 3
    assert nvme.max_online == 12
    assert nvme.default_online == 5
    assert nvme.cooldown_secs == 60


# ---------------------------------------------------------------------------
# HTTP layer tests via FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient for the autoscaler pools router with an admin principal injected.

    The config PATCH route requires ``fleet.control`` (issue #924); these tests
    exercise the config logic, so we inject an admin via dependency_overrides and
    assert the auth gate separately in ``test_patch_pool_config_requires_auth``.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import Principal, require_principal
    from routers.autoscaler_pools import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-admin", type="bot", name="Admin", roles=["admin"]
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_patch_pool_config_requires_auth() -> None:
    """POST /api/autoscaler/pools/{pool}/config rejects unauthenticated callers (#924)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.autoscaler_pools import router
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # pragma: allowlist secret
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/autoscaler/pools/nvme/config", json={"min_online": 1, "max_online": 4})
    assert resp.status_code == 401


def test_get_autoscaler_pools_returns_200(client) -> None:
    """GET /api/autoscaler/pools returns HTTP 200."""
    resp = client.get("/api/autoscaler/pools")
    assert resp.status_code == 200


def test_get_autoscaler_pools_response_shape(client) -> None:
    """Response body has pools list, cooldown_secs, and dry_run keys."""
    resp = client.get("/api/autoscaler/pools")
    data = resp.json()
    assert "pools" in data
    assert "cooldown_secs" in data
    assert "dry_run" in data


def test_get_autoscaler_pools_three_entries(client) -> None:
    """Response contains exactly three pool entries."""
    resp = client.get("/api/autoscaler/pools")
    data = resp.json()
    assert len(data["pools"]) == 3


def test_get_autoscaler_pools_pool_names(client) -> None:
    """Response pool names are nvme, hdd, default."""
    resp = client.get("/api/autoscaler/pools")
    data = resp.json()
    names = {p["pool"] for p in data["pools"]}
    assert names == {"nvme", "hdd", "default"}


def test_get_autoscaler_pools_pool_fields(client) -> None:
    """Each pool entry has required scaling fields."""
    resp = client.get("/api/autoscaler/pools")
    data = resp.json()
    required_fields = {
        "pool",
        "min_online",
        "max_online",
        "default_online",
        "pattern",
        "labels",
        "start_enabled",
        "stop_enabled",
        "pressure_metric",
        "cooldown_secs",
    }
    for pool in data["pools"]:
        missing = required_fields - set(pool.keys())
        assert not missing, f"Pool {pool['pool']!r} missing fields: {missing}"


def test_patch_pool_config_valid(client) -> None:
    """POST /api/autoscaler/pools/nvme/config with valid body returns 200."""
    resp = client.post(
        "/api/autoscaler/pools/nvme/config",
        json={"min_online": 2, "max_online": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pool"] == "nvme"
    assert data["min_online"] == 2
    assert data["max_online"] == 10
    assert data["status"] == "applied"


def test_patch_pool_config_hdd(client) -> None:
    """POST /api/autoscaler/pools/hdd/config with valid body returns 200."""
    resp = client.post(
        "/api/autoscaler/pools/hdd/config",
        json={"min_online": 1, "max_online": 8},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pool"] == "hdd"


def test_patch_pool_config_unknown_pool_rejected(client) -> None:
    """POST /api/autoscaler/pools/unknown/config returns 422."""
    resp = client.post(
        "/api/autoscaler/pools/unknown/config",
        json={"min_online": 1, "max_online": 5},
    )
    assert resp.status_code == 422


def test_patch_pool_config_min_exceeds_max_rejected(client) -> None:
    """POST returns 422 when min_online > max_online."""
    resp = client.post(
        "/api/autoscaler/pools/nvme/config",
        json={"min_online": 10, "max_online": 5},
    )
    assert resp.status_code == 422


def test_patch_pool_config_negative_min_rejected(client) -> None:
    """POST returns 422 when min_online is negative (pydantic ge=0 constraint)."""
    resp = client.post(
        "/api/autoscaler/pools/nvme/config",
        json={"min_online": -1, "max_online": 5},
    )
    assert resp.status_code == 422


def test_patch_pool_config_updates_env_vars(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/autoscaler/pools/nvme/config updates env vars as a side-effect."""
    import os

    # Clear any prior override
    monkeypatch.delenv("AUTOSCALER_NVME_MIN_ONLINE", raising=False)
    monkeypatch.delenv("AUTOSCALER_NVME_MAX_ONLINE", raising=False)

    resp = client.post(
        "/api/autoscaler/pools/nvme/config",
        json={"min_online": 3, "max_online": 14},
    )
    assert resp.status_code == 200
    assert os.environ.get("AUTOSCALER_NVME_MIN_ONLINE") == "3"
    assert os.environ.get("AUTOSCALER_NVME_MAX_ONLINE") == "14"


def test_patch_pool_config_default_pool(client) -> None:
    """POST /api/autoscaler/pools/default/config is accepted."""
    resp = client.post(
        "/api/autoscaler/pools/default/config",
        json={"min_online": 1, "max_online": 20},
    )
    assert resp.status_code == 200


def test_get_pools_cooldown_is_integer(client) -> None:
    """cooldown_secs in the response is a non-negative integer."""
    resp = client.get("/api/autoscaler/pools")
    data = resp.json()
    assert isinstance(data["cooldown_secs"], int)
    assert data["cooldown_secs"] >= 0


def test_get_pools_dry_run_is_bool(client) -> None:
    """dry_run in the response is a boolean."""
    resp = client.get("/api/autoscaler/pools")
    data = resp.json()
    assert isinstance(data["dry_run"], bool)
