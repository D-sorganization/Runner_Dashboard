"""TDD tests for the live per-provider model catalog and POST /api/models/refresh (#3533)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from model_catalog import (  # noqa: E402
    CATALOG_PROVIDERS,
    DAILY_REFRESH_INTERVAL_SECONDS,
    get_catalog,
    parse_provider_models,
    refresh_catalog,
    reset_model_catalog,
)
from server import app  # noqa: E402
from staff.model_tiers import allowed_models_for_provider, validate_role_model  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_catalog() -> None:
    reset_model_catalog(seed=True, now=1000.0)


@pytest.fixture
def client(mock_auth: object) -> TestClient:  # noqa: ARG001
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


def test_parse_provider_models_for_all_providers() -> None:
    assert parse_provider_models("codex", '{"models": [{"id": "gpt-6.1-terra"}, {"id": "gpt-6-luna"}]}') == [
        "gpt-6.1-terra",
        "gpt-6-luna",
    ]
    assert parse_provider_models("claude", '[{"name": "claude-opus-5-5"}, {"name": "sonnet"}]') == [
        "claude-opus-5-5",
        "sonnet",
    ]
    assert parse_provider_models("cursor-agent", "MODEL ID\n---\ncomposer-2.5\ngrok-4.7-high\n") == [
        "composer-2.5",
        "grok-4.7-high",
    ]
    assert parse_provider_models("antigravity", "* gemini-3.8-flash-high\n* gemini-4-argon-high\n") == [
        "gemini-3.8-flash-high",
        "gemini-4-argon-high",
    ]
    assert parse_provider_models("gemini", '{"data": ["gemini-2.5-pro", "gemini-3.8-flash"]}') == [
        "gemini-2.5-pro",
        "gemini-3.8-flash",
    ]
    assert parse_provider_models("grok", "composer-2.5\ngrok-4.7-high\ngrok-4.7-xhigh\n") == [
        "grok-4.7-high",
        "grok-4.7-xhigh",
    ]


def test_fresh_hub_catalog_and_registry_expose_current_models(client: TestClient) -> None:
    body = client.get("/api/models/catalog").json()
    for pid in CATALOG_PROVIDERS:
        assert len(body["providers"][pid]) > 0
    assert "gpt-6.1-terra" in body["providers"]["codex"]
    assert "claude-opus-5-5" in body["providers"]["claude"]
    by_dash = {p["dashboard_id"]: p for p in client.get("/api/providers/registry").json()["providers"]}
    assert "gpt-6.1-terra" in by_dash["codex_cli"]["models"]
    assert "claude-opus-5-5" in by_dash["claude_code_cli"]["models"]


def test_refresh_updates_catalog_and_dispatch_validator(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import model_catalog

    monkeypatch.setattr(
        model_catalog,
        "probe_cli_models",
        lambda pid: [f"{pid}-live-2026"] if pid != "grok" else ["grok-4.8-live"],
    )
    data = client.post("/api/models/refresh").json()
    assert data["stale"] is False
    assert "codex-live-2026" in data["providers"]["codex"]
    assert "grok-4.8-live" in data["providers"]["grok"]
    assert "codex-live-2026" in allowed_models_for_provider("codex")
    assert validate_role_model("codex", "codex-live-2026") == []


def test_daily_auto_refresh_and_last_good_cache_fallback(tmp_path: Path) -> None:
    cache_file = tmp_path / "catalog.json"
    reset_model_catalog(seed=False, now=1000.0, cache_path=cache_file)
    snap1 = refresh_catalog(fetcher=lambda pid: ["grok-4.7-high"] if pid == "grok" else [f"{pid}-v1"], now=1000.0)
    assert snap1["stale"] is False and snap1["age_seconds"] == 0.0
    assert json.loads(cache_file.read_text(encoding="utf-8"))["providers"]["codex"] == ["codex-v1"]

    snap_mid = get_catalog(
        now=4600.0,
        fetcher=lambda _pid: (_ for _ in ()).throw(AssertionError("should not refresh before 24h")),
    )
    assert snap_mid["age_seconds"] == 3600.0 and snap_mid["providers"]["codex"] == ["codex-v1"]

    def failing_fetcher(_pid: str) -> list[str]:
        raise RuntimeError("CLI probe failed")

    snap_fail = get_catalog(now=1000.0 + DAILY_REFRESH_INTERVAL_SECONDS, fetcher=failing_fetcher)
    assert snap_fail["stale"] is True and snap_fail["age_seconds"] == DAILY_REFRESH_INTERVAL_SECONDS
    assert snap_fail["providers"]["codex"] == ["codex-v1"]
    assert "CLI probe failed" in snap_fail["last_error"]["codex"]

    reset_model_catalog(seed=False, now=2000.0)
    unseeded_fail = refresh_catalog(fetcher=failing_fetcher, now=2000.0)
    assert unseeded_fail["stale"] is True and unseeded_fail["providers"]["codex"] == []
