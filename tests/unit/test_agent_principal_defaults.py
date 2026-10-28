"""agent_principal_defaults merge and token ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_principal_defaults import merge_agent_principal_defaults
from identity import IdentityManager, principal_has_scope

_MOCK_OVERLAY = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "overlay" / "mock-overlay.json"


def _defaults_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"


def _write_overlay(tmp_path: Path, agent_token_paths: dict[str, str]) -> Path:
    data = json.loads(_MOCK_OVERLAY.read_text(encoding="utf-8"))
    data["agent_token_paths"] = agent_token_paths
    overlay_path = tmp_path / "overlay.json"
    overlay_path.write_text(json.dumps(data), encoding="utf-8")
    return overlay_path


def test_merge_applies_fleet_read_to_agent_grok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(_defaults_path()))
    id_dir = tmp_path / "id"
    id_dir.mkdir()
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    (tok_dir / "agent-grok.token").write_text("svc_test_grok_ingest\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)

    prin = mgr.principals["agent-grok"]
    assert prin.roles == ["bot"]
    assert principal_has_scope(prin, "fleet.read")
    assert mgr.verify_token("svc_test_grok_ingest") is prin


def test_overlay_token_path_is_ingested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#3082 category 2: an overlay-declared token path is read even with no local directory."""
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(_defaults_path()))
    id_dir = tmp_path / "id"
    id_dir.mkdir()

    token_path = tmp_path / "overlay-store" / "agent-grok.token"
    token_path.parent.mkdir()
    token_path.write_text("svc_test_grok_overlay\n", encoding="utf-8")
    monkeypatch.setenv("RD_OVERLAY_PATH", str(_write_overlay(tmp_path, {"agent-grok": str(token_path)})))

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)

    prin = mgr.principals["agent-grok"]
    assert mgr.verify_token("svc_test_grok_overlay") is prin


def test_overlay_token_path_overrides_directory_glob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When both are configured, the overlay path wins over the local directory glob."""
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(_defaults_path()))
    id_dir = tmp_path / "id"
    id_dir.mkdir()
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    (tok_dir / "agent-grok.token").write_text("svc_test_grok_directory\n", encoding="utf-8")

    token_path = tmp_path / "overlay-store" / "agent-grok.token"
    token_path.parent.mkdir()
    token_path.write_text("svc_test_grok_overlay\n", encoding="utf-8")
    monkeypatch.setenv("RD_OVERLAY_PATH", str(_write_overlay(tmp_path, {"agent-grok": str(token_path)})))

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)

    prin = mgr.principals["agent-grok"]
    assert mgr.verify_token("svc_test_grok_overlay") is prin
    assert mgr.verify_token("svc_test_grok_directory") is None


def test_no_overlay_configured_falls_back_to_directory_glob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset RD_OVERLAY_PATH: behaviour is unchanged (overlay_contract's backward-compat contract)."""
    monkeypatch.delenv("RD_OVERLAY_PATH", raising=False)
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(_defaults_path()))
    id_dir = tmp_path / "id"
    id_dir.mkdir()
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    (tok_dir / "agent-grok.token").write_text("svc_test_grok_directory\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)

    prin = mgr.principals["agent-grok"]
    assert mgr.verify_token("svc_test_grok_directory") is prin
