"""Hold reconcile from Repository_Management role YAML (RM role sync)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import staff.holds as holds_mod
import staff.roles as roles_mod
from staff.audit import StaffAuditStore
from staff.rm_reconcile import HoldReconcileError, reconcile_holds, reconcile_holds_for_roles_dir
from staff.roles import parse_role


def _roles(**holds_by_role: list[str]) -> dict[str, roles_mod.RoleSpec]:
    out: dict[str, roles_mod.RoleSpec] = {}
    for name, holds in holds_by_role.items():
        out[name] = parse_role({"name": name, "providers": ["fake"], "holds": holds})
    return out


@pytest.mark.unit
def test_reconcile_adds_missing_yaml_holds(tmp_path: Path) -> None:
    path = tmp_path / "staff_holds.json"
    holds = holds_mod.HoldsList(path, roles_loader=dict)
    holds.replace([{"text": "freeze deploys", "kind": "schedule"}])
    diff = reconcile_holds(roles=_roles(**{"night-watch": ["never merge"]}), commit_sha="abc123", holds=holds)
    assert diff.added == ["never merge"]
    saved = {h.text: h for h in holds.load()}
    assert saved["never merge"].active and saved["never merge"].kind == "guardrail"
    assert saved["freeze deploys"].active


@pytest.mark.unit
def test_reconcile_deactivates_guardrail_removed_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "staff_holds.json"
    holds = holds_mod.HoldsList(path, roles_loader=dict)
    holds.replace(
        [
            {"text": "stale rule", "kind": "guardrail", "active": True},
            {"text": "still here", "kind": "guardrail", "active": True},
        ]
    )
    diff = reconcile_holds(roles=_roles(steward=["still here"]), commit_sha="deadbeef", holds=holds)
    assert diff.deactivated == ["stale rule"]
    by_text = {h.text: h for h in holds.load()}
    assert by_text["stale rule"].active is False
    assert by_text["still here"].active is True


@pytest.mark.unit
def test_reconcile_no_op_when_yaml_matches(tmp_path: Path) -> None:
    path = tmp_path / "staff_holds.json"
    roles = _roles(**{"night-watch": ["never merge"]})
    holds = holds_mod.HoldsList(path, roles_loader=dict)
    holds.replace([h.to_dict() for h in holds_mod.seed_from_roles(roles)])
    before = path.read_text(encoding="utf-8")
    diff = reconcile_holds(roles=roles, commit_sha="ccc", holds=holds, dry_run=False)
    assert not diff.changed
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_reconcile_dry_run_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "staff_holds.json"
    holds = holds_mod.HoldsList(path, roles_loader=dict)
    holds.replace([])
    before = path.read_text(encoding="utf-8")
    diff = reconcile_holds(roles=_roles(worker=["new hold"]), commit_sha="sha", holds=holds, dry_run=True)
    assert diff.added == ["new hold"]
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_reconcile_fail_closed_on_yaml_parse_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "broken.yml").write_text("name: [unclosed\n", encoding="utf-8")
    (roles_dir / "good.yml").write_text("name: good\nproviders: [claude]\n", encoding="utf-8")
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    holds_path = tmp_path / "staff_holds.json"
    holds_mod.HoldsList(holds_path).replace([{"text": "keep", "kind": "guardrail"}])
    with pytest.raises(HoldReconcileError, match="YAML syntax"):
        reconcile_holds_for_roles_dir(roles_dir, "commit", holds=holds_mod.HoldsList(holds_path))
    assert json.loads(holds_path.read_text(encoding="utf-8"))["holds"][0]["text"] == "keep"


@pytest.mark.unit
def test_reconcile_writes_audit_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit_db = tmp_path / "audit.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(audit_db))
    holds = holds_mod.HoldsList(tmp_path / "staff_holds.json", roles_loader=dict)
    holds.replace([{"text": "gone", "kind": "guardrail"}])
    reconcile_holds(roles=_roles(), commit_sha="abc123", holds=holds)
    store = StaffAuditStore(audit_db)
    lifts = store.list_entries(action="hold_lift")
    assert len(lifts) == 1
    assert lifts[0].principal == "role-sync"
    assert lifts[0].detail.get("source_commit") == "abc123"
