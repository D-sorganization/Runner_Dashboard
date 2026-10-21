"""agent-codex-hands principal defaults and scope boundaries."""

from __future__ import annotations

from pathlib import Path

import identity
import pytest
from agent_principal_defaults import merge_agent_principal_defaults
from identity import IdentityManager, principal_has_scope


def test_codex_hands_scopes_and_denies_operator_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    id_dir = tmp_path / "id"
    id_dir.mkdir()
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    (tok_dir / "agent-codex-hands.token").write_text("svc_codex_hands_ci_token\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)

    prin = mgr.principals["agent-codex-hands"]
    assert prin.assistant_kind == "codex"
    assert principal_has_scope(prin, "fleet.read")
    assert principal_has_scope(prin, "staff.dispatch")
    assert principal_has_scope(prin, "memory.read")
    assert principal_has_scope(prin, "memory.write")
    assert not principal_has_scope(prin, "staff.holds.write")
    assert not principal_has_scope(prin, "priorities.write")


def test_agent_grok_has_grokbot_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    id_dir = tmp_path / "id"
    id_dir.mkdir()
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir()
    (tok_dir / "agent-grok.token").write_text("svc_grok_kind\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)
    assert mgr.principals["agent-grok"].assistant_kind == "grokbot"
