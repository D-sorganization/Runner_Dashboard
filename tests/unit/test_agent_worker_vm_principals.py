"""agent-grok-vm and agent-duet principals: read + shared memory only (#3647)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_principal_defaults import merge_agent_principal_defaults
from identity import IdentityManager, principal_has_scope


@pytest.mark.parametrize(("pid", "kind"), [("agent-grok-vm", "grokbot"), ("agent-duet", "claude")])
def test_worker_vm_principals_least_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: str, kind: str
) -> None:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    tok_dir = tmp_path / "id" / "agent-tokens"
    tok_dir.mkdir(parents=True)
    (tok_dir / f"{pid}.token").write_text("svc_vm_worker_ci_token\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=tmp_path / "id")
    merge_agent_principal_defaults(mgr)

    prin = mgr.principals[pid]
    assert prin.assistant_kind == kind
    for scope in ("fleet.read", "memory.read", "memory.write"):
        assert principal_has_scope(prin, scope)
    for scope in ("staff.dispatch", "staff.holds.write", "priorities.write", "admin"):
        assert not principal_has_scope(prin, scope)
