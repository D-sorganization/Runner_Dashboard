"""Supervised dry-run and approval-gated activation contract tests (RD #3105, slice 11)."""

from __future__ import annotations

import json
from pathlib import Path

import orchestration_audit as _audit
import pytest
from staff.bot_builder import (
    ALL_MUTATING_SURFACES,
    ActivationError,
    DraftMode,
    GrantCeiling,
    MutatingSurface,
    RoleVersionLedger,
    ShadowDryRunSession,
    ShadowModeMutationRefused,
    approve_activation,
    create_draft,
    dispatch_task,
    is_material_change,
    material_change_fields,
    parse_builder_role,
    publish_draft,
)

_CEILING = GrantCeiling(
    approved_providers=frozenset({"sub-cli-alpha", "sub-cli-beta"}),
    allowed_capabilities=frozenset({"rd.staff_api", "rd.cli_sessions"}),
    household_capabilities=frozenset({"household.mail"}),
)


def _data(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "pilot-bot",
        "description": "Initial summary description.",
        "persona": "Alpha persona.",
        "instructions": "Answer briefly.",
        "skills": ["status-summary"],
        "model": "routine-default",
        "providers": ["sub-cli-alpha"],
        "schedule": "0 9 * * *",
        "triggers": [{"target_role": "night-watch", "event": "schedule_tick"}],
        "host": "grokbot-first",
        "enabled": True,
        "capabilities": ["rd.staff_api"],
        "private_context_ref": None,
        "household_data_opt_in": False,
        **overrides,
    }


@pytest.fixture(autouse=True)
def _audit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "orchestration_audit.ndjson"
    monkeypatch.setattr(_audit, "ORCHESTRATION_AUDIT_PATH", path)
    return path


@pytest.mark.asyncio
async def test_new_bot_starts_disabled_even_when_payload_requests_enabled() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    draft = create_draft(DraftMode.SCRATCH, _data(enabled=True))
    assert draft.role.enabled is False
    version = await publish_draft(draft, _CEILING, ledger, confirmed=False, principal="dieter")
    assert version.role.enabled is False and ledger.is_active is False


@pytest.mark.parametrize(
    ("field_name", "override"),
    [
        ("persona", {"persona": "Beta persona."}),
        ("instructions", {"instructions": "Answer in detail."}),
        ("skills", {"skills": ["status-summary", "triage"]}),
        ("model", {"model": "deep-review"}),
        ("providers", {"providers": ["sub-cli-beta"]}),
        ("capabilities", {"capabilities": ["rd.staff_api", "rd.cli_sessions"]}),
        ("host", {"host": "rd-first"}),
        ("schedule", {"schedule": "0 12 * * *"}),
        ("triggers", {"triggers": [{"target_role": "ops-watch", "event": "alert"}]}),
    ],
)
def test_material_change_detector_flags_every_material_surface(field_name: str, override: dict[str, object]) -> None:
    baseline = parse_builder_role(_data())
    candidate = parse_builder_role(_data(**override))
    assert is_material_change(baseline, candidate) is True
    assert field_name in material_change_fields(baseline, candidate)


def test_non_material_description_edit_is_not_flagged_as_material() -> None:
    baseline = parse_builder_role(_data(description="Typo in descrption."))
    candidate = parse_builder_role(_data(description="Typo in description."))
    assert is_material_change(baseline, candidate) is False
    assert material_change_fields(baseline, candidate) == ()


def test_shadow_mode_refuses_every_mutating_surface_and_records_would_be_actions() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    ledger.publish(parse_builder_role(_data(enabled=False)))
    session = ShadowDryRunSession(ledger)
    side_effects: list[str] = []
    mutator = lambda: side_effects.append("mutated")  # noqa: E731

    actions = (
        session.board_post("Post status", target="RM#2376", mutator=mutator),
        session.thread_reply("Reply thread", target="th_1", mutator=mutator),
        session.pr_action("Comment PR", target="RD#3105", mutator=mutator),
        session.context_write("Write ctx", target="pilot-bot/ctx", mutator=mutator),
        session.dispatch("Spawn task", target="night-watch", mutator=mutator),
    )
    assert side_effects == [] and all(rec.refused is True for rec in actions)
    with pytest.raises(ShadowModeMutationRefused):
        session.attempt_mutation(MutatingSurface.BOARD_POST, "Strict check", mutator=mutator, raise_on_refuse=True)
    dry_run = session.finish()
    assert side_effects == [] and dry_run.completed is True and len(dry_run.recorded_actions) == 6
    assert {item.surface for item in dry_run.recorded_actions} == set(ALL_MUTATING_SURFACES)


@pytest.mark.asyncio
async def test_activation_requires_recorded_approval_and_dispatch_refused_before_it(_audit_file: Path) -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    await publish_draft(create_draft(DraftMode.SCRATCH, _data()), _CEILING, ledger, confirmed=False, principal="dieter")
    with pytest.raises(ActivationError, match="not approved"):
        dispatch_task(ledger)

    session = ShadowDryRunSession(ledger)
    session.board_post("Dry-run check-in", target="RM#2376")
    dry_run = session.finish()

    with pytest.raises(ActivationError, match="not approved"):
        dispatch_task(ledger)
    with pytest.raises(ActivationError, match="dieter"):
        await approve_activation(ledger, dry_run, approver="other-operator")

    approval = await approve_activation(ledger, dry_run, approver="dieter", notes="Reviewed shadow dry-run.")
    assert approval.approver == "dieter" and ledger.is_active is True
    assert ledger.current is not None and ledger.current.role.enabled is True
    assert dispatch_task(ledger).pinned_version_id == ledger.current.version_id
    entries = [json.loads(line) for line in _audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e.get("action") == "bot_builder.activate" for e in entries)


@pytest.mark.asyncio
async def test_non_material_edit_preserves_activation_while_material_edit_resets_it() -> None:
    ledger = RoleVersionLedger(role_id="pilot-bot")
    v1_draft = create_draft(DraftMode.SCRATCH, _data(description="Typo."))
    await publish_draft(v1_draft, _CEILING, ledger, confirmed=False, principal="dieter")
    session = ShadowDryRunSession(ledger)
    session.thread_reply("Dry-run reply", target="th_1")
    dry_run_v1 = session.finish()
    await approve_activation(ledger, dry_run_v1, approver="dieter")

    v2 = await publish_draft(
        create_draft(DraftMode.EDIT, _data(description="Fixed typo."), ledger=ledger),
        _CEILING,
        ledger,
        confirmed=False,
        principal="dieter",
    )
    assert v2.role.enabled is True and ledger.is_active is True
    assert dispatch_task(ledger).pinned_version_id == v2.version_id

    v3_draft = create_draft(DraftMode.EDIT, _data(schedule="0 18 * * *"), ledger=ledger)
    assert v3_draft.role.enabled is False
    v3 = await publish_draft(v3_draft, _CEILING, ledger, confirmed=True, principal="dieter")
    assert v3.role.enabled is False and ledger.is_active is False
    with pytest.raises(ActivationError, match="not approved"):
        dispatch_task(ledger)
    with pytest.raises(ActivationError, match="version"):
        await approve_activation(ledger, dry_run_v1, approver="dieter")
