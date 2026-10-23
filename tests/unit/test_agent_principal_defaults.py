"""agent_principal_defaults merge and token ingestion."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_principal_defaults import merge_agent_principal_defaults
from identity import IdentityManager, principal_has_scope


def test_merge_applies_fleet_read_to_agent_grok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
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
