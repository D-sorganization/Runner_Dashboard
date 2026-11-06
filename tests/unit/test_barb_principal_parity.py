"""Barb lane principals share the same effective capability set (shared ``barb`` role preset).

Follow-up to #4117 (#4122):
1. Barb principal set is derived from config/agent_principals.yml.
2. BARB_SEAT_IDS is tied to the derived Barb principal set.
3. Seat status follows the barb role across all configured principals.
4. BARB_ROLE_SCOPES is pinned to the reviewed scope set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agent_principal_defaults import merge_agent_principal_defaults
from identity import BARB_ROLE_SCOPES, SCOPE_PRESETS, IdentityManager, principal_has_scope
from staff.barb_lease_fence import BARB_ID_SUFFIXES, BARB_SEAT_IDS, is_barb_seat

DEFAULTS_FILE = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"


def _configured_barb_principal_ids(defaults_path: Path = DEFAULTS_FILE) -> frozenset[str]:
    """Derive the set of principal IDs with the 'barb' role from config/agent_principals.yml (#4122 gap 1)."""
    data = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
    ids: set[str] = set()
    for row in data.get("agents", []):
        if isinstance(row, dict) and "barb" in (row.get("roles") or []):
            agent_id = row.get("id")
            if agent_id:
                ids.add(str(agent_id))
    return frozenset(ids)


BARB_PRINCIPAL_IDS: tuple[str, ...] = tuple(sorted(_configured_barb_principal_ids()))

REVIEWED_BARB_ROLE_SCOPES: frozenset[str] = frozenset(
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


def _barb_capability_set(mgr: IdentityManager, principal_id: str) -> frozenset[str]:
    prin = mgr.principals[principal_id]
    direct = frozenset(prin.scopes)
    preset: set[str] = set()
    for role in prin.roles:
        preset.update(SCOPE_PRESETS.get(role, []))
    allowed = {s for s in preset if principal_has_scope(prin, s)} | {s for s in direct if principal_has_scope(prin, s)}
    return frozenset(allowed)


def test_barb_principals_derived_from_config() -> None:
    """The parity test's Barb principal set is derived from config/agent_principals.yml (#4122 gap 1)."""
    derived = _configured_barb_principal_ids()
    assert derived, "config/agent_principals.yml must declare at least one Barb principal"
    assert "agent-grok" in derived
    assert "agent-codex-hands" in derived
    assert frozenset(BARB_PRINCIPAL_IDS) == derived


def test_barb_seat_ids_tied_to_configured_barb_principals() -> None:
    """The fence's BARB_SEAT_IDS must stay in exact sync with configured Barb principals (#4122 gap 2)."""
    derived = _configured_barb_principal_ids()
    # BARB_SEAT_IDS includes the configured lane principals plus the legacy abstract 'barb' seat.
    assert BARB_SEAT_IDS - {"barb"} == derived
    assert "barb" in BARB_SEAT_IDS
    assert BARB_SEAT_IDS == derived | {"barb"}


def test_seat_status_follows_barb_role_for_all_configured_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In config/agent_principals.yml, is_barb_seat() is True iff principal has the 'barb' role (#4122 gap 3)."""
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(DEFAULTS_FILE))
    tok_dir = tmp_path / "id" / "agent-tokens"
    tok_dir.mkdir(parents=True)
    raw_cfg = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8")) or {}
    for row in raw_cfg.get("agents", []):
        if isinstance(row, dict) and row.get("id"):
            (tok_dir / f"{row['id']}.token").write_text("tok\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=tmp_path / "id")
    merge_agent_principal_defaults(mgr)

    assert len(mgr.principals) > 0
    for pid, prin in mgr.principals.items():
        has_barb_role = "barb" in prin.roles or "barb" in prin.scopes
        assert is_barb_seat(prin) is has_barb_role, (
            f"Principal {pid!r} seat status ({is_barb_seat(prin)}) does not match barb role ({has_barb_role})"
        )


def test_no_configured_agent_gains_barb_seat_status_by_suffix_without_role() -> None:
    """A principal matching a Barb suffix or seat ID must not be configured without the barb role (#4122 gap 3)."""
    raw_cfg = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8")) or {}
    for row in raw_cfg.get("agents", []):
        if not isinstance(row, dict) or not row.get("id"):
            continue
        pid = str(row["id"])
        roles = row.get("roles") or []
        has_barb_role = "barb" in roles
        matches_suffix = any(pid.endswith(suffix) for suffix in BARB_ID_SUFFIXES)
        in_seat_ids = pid in BARB_SEAT_IDS

        if matches_suffix or in_seat_ids:
            assert has_barb_role, (
                f"Principal {pid!r} matches Barb suffix/seat ID but lacks 'barb' role in agent_principals.yml"
            )


def test_barb_role_scopes_pins_reviewed_scope_set() -> None:
    """BARB_ROLE_SCOPES must match the exact reviewed scope set to prevent unreviewed widening (#4122 gap 4)."""
    assert frozenset(BARB_ROLE_SCOPES) == REVIEWED_BARB_ROLE_SCOPES
    # Explicitly ensure sensitive/admin scopes remain absent
    assert "staff.approve" not in BARB_ROLE_SCOPES
    assert "*" not in BARB_ROLE_SCOPES
    assert "admin" not in BARB_ROLE_SCOPES
    assert "operator" not in BARB_ROLE_SCOPES


def test_barb_principals_match_agent_grok_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(DEFAULTS_FILE))
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
    assert "staff.approve" not in ref
    assert "staff.write" in ref


def test_barb_principals_never_gain_admin_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(DEFAULTS_FILE))
    tok_dir = tmp_path / "id" / "agent-tokens"
    tok_dir.mkdir(parents=True)
    (tok_dir / "agent-grok.token").write_text("svc_test\n", encoding="utf-8")

    mgr = IdentityManager(config_dir=tmp_path / "id")
    merge_agent_principal_defaults(mgr)
    prin = mgr.principals["agent-grok"]
    for scope in ("admin", "operator"):
        assert not principal_has_scope(prin, scope)
