"""Barb lane principals share the same effective capability set (shared ``barb`` role preset)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from agent_principal_defaults import merge_agent_principal_defaults
from identity import BARB_ROLE_SCOPES, SCOPE_PRESETS, IdentityManager, principal_has_scope
from staff.barb_lease_fence import BARB_SEAT_IDS, is_barb_seat

BARB_PRINCIPAL_IDS = (
    "agent-grok",
    "agent-grok-vm",
    "agent-duet",
    "agent-codex-hands",
    "grok-barb",
    "rd-barb",
    "agent-claude-barb",
    "agent-codex-barb",
    "agent-cursor-barb",
    "agent-agy-barb",
)


def _barb_capability_set(mgr: IdentityManager, principal_id: str) -> frozenset[str]:
    prin = mgr.principals[principal_id]
    direct = frozenset(prin.scopes)
    preset: set[str] = set()
    for role in prin.roles:
        preset.update(SCOPE_PRESETS.get(role, []))
    allowed = {s for s in preset if principal_has_scope(prin, s)} | {s for s in direct if principal_has_scope(prin, s)}
    return frozenset(allowed)


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

    ref = _barb_capability_set(mgr, "agent-grok")
    for scope in BARB_ROLE_SCOPES:
        assert scope in ref

    for pid in BARB_PRINCIPAL_IDS:
        prin = mgr.principals[pid]
        assert prin.roles[:2] == ["bot", "barb"], pid
        assert _barb_capability_set(mgr, pid) == ref, pid
    assert "staff.approve" in ref
    assert "staff.write" in ref


def test_barb_principals_never_gain_admin_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    tok_dir = tmp_path / "id" / "agent-tokens"
    tok_dir.mkdir(parents=True)
    (tok_dir / "agent-grok.token").write_text("svc_test\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=tmp_path / "id")
    merge_agent_principal_defaults(mgr)
    prin = mgr.principals["agent-grok"]
    for scope in ("admin", "operator"):
        assert not principal_has_scope(prin, scope)


# Reviewed Barb capability set (RD #4117, widened to add staff.approve by RD #4148). Pinned so a
# change to the operator preset cannot widen every Barb seat without a deliberate edit here;
# widening it is an owner decision, not a lane one.
REVIEWED_BARB_ROLE_SCOPES = frozenset(
    {
        "activity.read",
        "activity.write",
        "assessments.dispatch",
        "assistant.chat",
        "assistant.execute",
        "code-requests.manage",
        "coordination.write",
        "feature-requests.manage",
        "fleet.control",
        "fleet.maintain",
        "fleet.read",
        "github.app_token",
        "github.dispatch",
        "heavy-tests.dispatch",
        "memory.read",
        "memory.write",
        "priorities.write",
        "proposals.write",
        "remediation.dispatch",
        "runners.control",
        "staff.admin",
        "staff.approve",
        "staff.audit.read",
        "staff.cancel",
        "staff.chat",
        "staff.dispatch",
        "staff.dispatcher.ledger.write",
        "staff.github_credential",
        "staff.holds.write",
        "staff.read",
        "staff.write",
        "system.control",
        "tests.rerun",
        "workflows.control",
        "workflows.dispatch",
    }
)


def _default_principal_entries() -> list[dict[str, object]]:
    defaults = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
    data = yaml.safe_load(defaults.read_text(encoding="utf-8"))
    return [entry for entry in data["agents"] if isinstance(entry, dict) and "id" in entry]


def test_barb_role_scope_set_is_pinned() -> None:
    assert frozenset(BARB_ROLE_SCOPES) == REVIEWED_BARB_ROLE_SCOPES


def test_parity_list_covers_every_barb_role_principal() -> None:
    with_role = {str(e["id"]) for e in _default_principal_entries() if "barb" in (e.get("roles") or [])}
    assert with_role == set(BARB_PRINCIPAL_IDS)


def test_fence_seat_ids_match_parity_list() -> None:
    # "barb" is the role-name alias, not a principal.
    assert set(BARB_SEAT_IDS) - {"barb"} == set(BARB_PRINCIPAL_IDS)


def test_barb_seat_status_follows_the_barb_role() -> None:
    for entry in _default_principal_entries():
        roles = [str(r) for r in (entry.get("roles") or [])]
        scopes = [str(s) for s in (entry.get("scopes") or [])]
        stand_in = SimpleNamespace(id=str(entry["id"]), roles=roles, scopes=scopes)
        assert is_barb_seat(stand_in) == ("barb" in roles), entry["id"]  # type: ignore[arg-type]
