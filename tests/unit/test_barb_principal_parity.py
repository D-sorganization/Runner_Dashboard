"""Barb lane principals share the same effective scopes as agent-grok (shared ``barb`` role preset)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_principal_defaults import merge_agent_principal_defaults
from identity import BARB_ROLE_SCOPES, SCOPE_PRESETS, IdentityManager, principal_has_scope

BARB_PRINCIPAL_IDS = (
    "agent-grok",
    "agent-grok-vm",
    "agent-duet",
    "agent-codex-hands",
    "rd-barb",
    "agent-claude-barb",
    "agent-codex-barb",
    "agent-cursor-barb",
    "agent-agy-barb",
)


def _expanded_preset_scopes(principal_id: str, mgr: IdentityManager) -> frozenset[str]:
    prin = mgr.principals[principal_id]
    seen: set[str] = set(prin.scopes)
    for role in prin.roles:
        seen.update(SCOPE_PRESETS.get(role, []))
    return frozenset(seen)


def test_barb_principals_match_agent_grok_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    tok_dir = tmp_path / "id" / "agent-tokens"
    tok_dir.mkdir(parents=True)
    raw = "svc_barb_parity_test_token\n"
    for pid in BARB_PRINCIPAL_IDS:
        (tok_dir / f"{pid}.token").write_text(raw, encoding="utf-8")

    mgr = IdentityManager(config_dir=tmp_path / "id")
    merge_agent_principal_defaults(mgr)

    ref = _expanded_preset_scopes("agent-grok", mgr)
    for scope in BARB_ROLE_SCOPES:
        assert scope in ref

    for pid in BARB_PRINCIPAL_IDS:
        prin = mgr.principals[pid]
        assert prin.roles[:2] == ["bot", "barb"], pid
        assert _expanded_preset_scopes(pid, mgr) == ref, pid


def test_barb_principals_never_gain_admin_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    tok_dir = tmp_path / "id" / "agent-tokens"
    tok_dir.mkdir(parents=True)
    (tok_dir / "agent-grok.token").write_text("svc_test\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=tmp_path / "id")
    merge_agent_principal_defaults(mgr)
    prin = mgr.principals["agent-grok"]
    for scope in ("admin", "staff.admin", "operator"):
        assert not principal_has_scope(prin, scope)
