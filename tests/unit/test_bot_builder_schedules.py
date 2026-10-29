"""Bounded schedules/triggers and declared-edge cycle validation tests (RD #3107, slice 13)."""

from __future__ import annotations

from pathlib import Path

import orchestration_audit as _audit
import pytest
from staff.bot_builder import (
    DEFAULT_SCHEDULE_POLICY,
    DraftMode,
    GrantCeiling,
    PublishError,
    RoleVersionLedger,
    ScheduleBounds,
    SchedulePolicy,
    TriggerEdge,
    can_run_at_hour,
    create_draft,
    escalation_diff,
    extract_declared_edges,
    parse_builder_role,
    publish_draft,
    validate_builder_role,
    validate_trigger_graph,
)

_CEILING = GrantCeiling(
    approved_providers=frozenset({"sub-cli-alpha"}),
    allowed_capabilities=frozenset({"rd.staff_api"}),
    household_capabilities=frozenset({"household.mail"}),
)


def _data(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "role-a",
        "persona": "Alpha persona.",
        "instructions": "Answer briefly.",
        "skills": ["status-summary"],
        "model": "routine-default",
        "providers": ["sub-cli-alpha"],
        "schedule": "0 9 * * *",
        "schedule_bounds": {
            "runs_per_day": 4,
            "concurrency": 1,
            "quiet_hours_utc": [0, 6],
        },
        "triggers": [],
        "host": "grokbot-first",
        "enabled": False,
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


def test_schedule_within_policy_bounds_and_outside_quiet_hours_passes() -> None:
    role = parse_builder_role(_data())
    assert role.schedule_bounds == ScheduleBounds(runs_per_day=4, concurrency=1, quiet_hours_utc=(0, 6))
    result = validate_builder_role(role, _CEILING)
    assert result.ok
    assert can_run_at_hour(role, 9, DEFAULT_SCHEDULE_POLICY) is True
    assert can_run_at_hour(role, 2, DEFAULT_SCHEDULE_POLICY) is False


@pytest.mark.parametrize(
    ("override", "expected_substr"),
    [
        (
            {"schedule_bounds": {"runs_per_day": 48, "concurrency": 1, "quiet_hours_utc": [0, 6]}},
            "max_runs_per_day",
        ),
        (
            {"schedule_bounds": {"runs_per_day": 4, "concurrency": 4, "quiet_hours_utc": [0, 6]}},
            "max_concurrency",
        ),
        (
            {"schedule_bounds": {"runs_per_day": 4, "concurrency": 1, "quiet_hours_utc": [2, 4]}},
            "quiet_hours",
        ),
        (
            {"schedule": "0 3 * * *"},
            "quiet_hours",
        ),
    ],
)
def test_schedule_above_policy_bounds_or_inside_quiet_hours_is_rejected(
    override: dict[str, object], expected_substr: str
) -> None:
    policy = SchedulePolicy(max_runs_per_day=24, max_concurrency=1, quiet_hours_utc=(0, 6))
    role = parse_builder_role(_data(**override))
    result = validate_builder_role(role, _CEILING, schedule_policy=policy)
    assert not result.ok
    assert any(expected_substr in reason for reason in result.reasons)


@pytest.mark.asyncio
async def test_self_widening_schedule_or_triggers_rejected_without_confirmation() -> None:
    ledger = RoleVersionLedger(role_id="role-a")
    await publish_draft(create_draft(DraftMode.SCRATCH, _data()), _CEILING, ledger, confirmed=False, principal="dieter")

    widened_draft = create_draft(
        DraftMode.EDIT,
        _data(
            schedule_bounds={"runs_per_day": 12, "concurrency": 1, "quiet_hours_utc": [0, 6]},
            triggers=[{"target_role": "role-b", "event": "done"}],
        ),
        ledger=ledger,
    )
    assert ledger.original_role is not None and ledger.current is not None
    diff = escalation_diff(ledger.original_role, ledger.current.role, widened_draft.role)
    assert "schedule_bounds" in diff.requires_confirmation
    assert "triggers" in diff.requires_confirmation

    # Widening without confirmation is rejected.
    with pytest.raises(PublishError, match="confirmation"):
        await publish_draft(widened_draft, _CEILING, ledger, confirmed=False, principal="dieter")

    # A bot cannot self-confirm its own bounds widening.
    with pytest.raises(PublishError, match="self-widening"):
        await publish_draft(widened_draft, _CEILING, ledger, confirmed=True, principal="role-a")

    # Owner confirmation succeeds.
    v2 = await publish_draft(widened_draft, _CEILING, ledger, confirmed=True, principal="dieter")
    assert v2.role.schedule_bounds is not None and v2.role.schedule_bounds.runs_per_day == 12


@pytest.mark.asyncio
async def test_cross_role_trigger_cycle_rejected_at_publish_and_reads_only_declared_edges() -> None:
    class _PeerRoleBoundarySpy:
        """Raises if the cycle validator attempts to read any non-edge content on a peer role."""

        def __init__(self, name: str, triggers: tuple[TriggerEdge, ...]) -> None:
            self.name = name
            self.triggers = triggers

        @property
        def persona(self) -> str:
            raise AssertionError("cycle validator must not read peer persona")

        @property
        def instructions(self) -> str:
            raise AssertionError("cycle validator must not read peer instructions")

        @property
        def private_context_ref(self) -> str:
            raise AssertionError("cycle validator must not read peer private_context_ref")

    peer_b = _PeerRoleBoundarySpy("role-b", (TriggerEdge(target_role="role-a", event="step_done"),))
    peer_c = _PeerRoleBoundarySpy("role-c", ())
    edge_map = extract_declared_edges([peer_b, peer_c])

    # Acyclic edge role-a -> role-c passes.
    acyclic = validate_trigger_graph("role-a", (TriggerEdge("role-c", "tick"),), edge_map)
    assert acyclic.ok

    # Cycle role-a -> role-b -> role-a fails without touching peer content properties.
    cyclic = validate_trigger_graph("role-a", (TriggerEdge("role-b", "tick"),), edge_map)
    assert not cyclic.ok
    assert any("cycle" in r for r in cyclic.reasons)

    # Publish rejects the cycle at publish time.
    ledger = RoleVersionLedger(role_id="role-a")
    cyclic_draft = create_draft(
        DraftMode.SCRATCH,
        _data(triggers=[{"target_role": "role-b", "event": "tick"}]),
    )
    with pytest.raises(PublishError, match="cycle"):
        await publish_draft(
            cyclic_draft,
            _CEILING,
            ledger,
            confirmed=True,
            principal="dieter",
            declared_edges_by_role=edge_map,
        )
