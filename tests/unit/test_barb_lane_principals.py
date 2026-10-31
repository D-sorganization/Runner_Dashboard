"""Dedicated Barb lane principals carry the Barb peer scopes (#3647)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_principal_defaults import merge_agent_principal_defaults
from identity import IdentityManager, principal_has_scope

LANES = ("rd-barb", "agent-claude-barb", "agent-codex-barb", "agent-cursor-barb", "agent-agy-barb")
PEER_SCOPES = (
    "fleet.read",
    "memory.read",
    "memory.write",
    "staff.read",
    "staff.dispatch",
    "staff.chat",
    "staff.cancel",
    "staff.holds.write",
    "priorities.write",
    "coordination.write",
    "proposals.write",
    "assessments.dispatch",
)


@pytest.fixture()
def principals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    id_dir = tmp_path / "id"
    tok_dir = id_dir / "agent-tokens"
    tok_dir.mkdir(parents=True)
    for pid in LANES:
        (tok_dir / f"{pid}.token").write_text(f"svc_{pid.replace('-', '_')}_ci\n", encoding="utf-8")
    mgr = IdentityManager(config_dir=id_dir)
    merge_agent_principal_defaults(mgr)
    return mgr.principals


@pytest.mark.parametrize("pid", LANES)
def test_lane_has_peer_scopes(principals, pid: str) -> None:
    prin = principals[pid]
    for scope in PEER_SCOPES:
        assert principal_has_scope(prin, scope), (pid, scope)
    for scope in ("admin", "staff.admin", "operator"):
        assert scope not in (prin.scopes or []), (pid, scope)


@pytest.mark.parametrize("pid", ("agent-claude", "agent-codex", "agent-gemini", "agent-cursor"))
def test_generic_agents_not_widened(principals, pid: str) -> None:
    prin = principals.get(pid)
    if prin is None:
        pytest.skip(f"{pid} not merged without a token file")
    assert not principal_has_scope(prin, "staff.dispatch")
    assert not principal_has_scope(prin, "staff.holds.write")
