"""Supervised shadow dry-run and approval-gated activation (RD #3105, slice 11)."""

from __future__ import annotations

import enum
import itertools
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from orchestration_audit import append_orchestration_audit
from staff.bot_builder.schema import BuilderRoleDefinition

if TYPE_CHECKING:
    from staff.bot_builder.versions import RoleVersionLedger

_APPROVED_ACTIVATION_PRINCIPALS: frozenset[str] = frozenset({"dieter"})


class MutatingSurface(enum.Enum):
    BOARD_POST = "board_post"
    THREAD_REPLY = "thread_reply"
    PR_ACTION = "pr_action"
    CONTEXT_WRITE = "context_write"
    DISPATCH = "dispatch"


ALL_MUTATING_SURFACES: tuple[MutatingSurface, ...] = tuple(MutatingSurface)


class ActivationError(RuntimeError):
    """Activation or dispatch was refused; message carries non-sensitive reasons only."""


class ShadowModeMutationRefused(RuntimeError):
    """Raised when strict refusal is requested on a shadow-mode mutating surface."""


@dataclass(frozen=True)
class DryRunRecordedAction:
    surface: MutatingSurface
    summary: str
    target: str
    refused: bool = True


@dataclass(frozen=True)
class DryRunRecord:
    run_id: str
    role_id: str
    version_id: str
    content_hash: str
    recorded_actions: tuple[DryRunRecordedAction, ...]
    completed: bool


@dataclass(frozen=True)
class ApprovalRecord:
    role_id: str
    version_id: str
    content_hash: str
    dry_run_id: str
    approver: str
    notes: str


def _norm_ws(text: str) -> str:
    return " ".join(text.split())


def material_change_fields(previous: BuilderRoleDefinition, candidate: BuilderRoleDefinition) -> tuple[str, ...]:
    """Return all material fields that differ between ``previous`` and ``candidate``."""
    caps_changed = (
        tuple(previous.capabilities) != tuple(candidate.capabilities)
        or previous.household_data_opt_in != candidate.household_data_opt_in
        or previous.private_context_ref != candidate.private_context_ref
    )
    checks: tuple[tuple[str, bool], ...] = (
        ("persona", _norm_ws(previous.persona) != _norm_ws(candidate.persona)),
        ("instructions", _norm_ws(previous.instructions) != _norm_ws(candidate.instructions)),
        ("skills", tuple(previous.skills) != tuple(candidate.skills)),
        ("model", previous.model != candidate.model),
        ("providers", tuple(previous.providers) != tuple(candidate.providers)),
        ("capabilities", caps_changed),
        ("host", previous.host != candidate.host),
        ("schedule", previous.schedule != candidate.schedule or previous.schedule_bounds != candidate.schedule_bounds),
        ("triggers", tuple(previous.triggers) != tuple(candidate.triggers)),
    )
    return tuple(name for name, diff in checks if diff)


def is_material_change(previous: BuilderRoleDefinition, candidate: BuilderRoleDefinition) -> bool:
    return bool(material_change_fields(previous, candidate))


class ShadowDryRunSession:
    """Supervised shadow-mode dry run with a hard no-mutation boundary."""

    _run_seq = itertools.count(start=1)

    def __init__(self, ledger: RoleVersionLedger) -> None:
        current = ledger.current
        if current is None:
            raise ValueError("shadow dry run requires a published role version")
        ledger.enable_activation_gate()
        self._ledger = ledger
        self._version = current
        self._run_id = f"dryrun-{next(self._run_seq)}-{current.version_id}"
        self._actions: list[DryRunRecordedAction] = []
        self._completed = False

    @property
    def recorded_actions(self) -> tuple[DryRunRecordedAction, ...]:
        return tuple(self._actions)

    def attempt_mutation(
        self,
        surface: MutatingSurface,
        summary: str,
        *,
        target: str = "",
        mutator: Callable[[], object] | None = None,
        raise_on_refuse: bool = False,
    ) -> DryRunRecordedAction:
        """DbC: never invokes ``mutator``; records the would-be action as refused."""
        if self._completed:
            raise RuntimeError("shadow dry-run session is already finalized")
        if not isinstance(surface, MutatingSurface) or not isinstance(summary, str) or not summary.strip():
            raise ValueError("valid MutatingSurface and non-empty summary required")
        _ = mutator
        recorded = DryRunRecordedAction(surface=surface, summary=summary.strip(), target=target.strip(), refused=True)
        self._actions.append(recorded)
        if raise_on_refuse:
            raise ShadowModeMutationRefused(f"shadow mode refused mutating surface: {surface.value}")
        return recorded

    def board_post(self, summary: str, **kwargs: Any) -> DryRunRecordedAction:
        return self.attempt_mutation(MutatingSurface.BOARD_POST, summary, **kwargs)

    def thread_reply(self, summary: str, **kwargs: Any) -> DryRunRecordedAction:
        return self.attempt_mutation(MutatingSurface.THREAD_REPLY, summary, **kwargs)

    def pr_action(self, summary: str, **kwargs: Any) -> DryRunRecordedAction:
        return self.attempt_mutation(MutatingSurface.PR_ACTION, summary, **kwargs)

    def context_write(self, summary: str, **kwargs: Any) -> DryRunRecordedAction:
        return self.attempt_mutation(MutatingSurface.CONTEXT_WRITE, summary, **kwargs)

    def dispatch(self, summary: str, **kwargs: Any) -> DryRunRecordedAction:
        return self.attempt_mutation(MutatingSurface.DISPATCH, summary, **kwargs)

    def finish(self) -> DryRunRecord:
        self._completed = True
        record = DryRunRecord(
            run_id=self._run_id,
            role_id=self._ledger.role_id,
            version_id=self._version.version_id,
            content_hash=self._version.content_hash,
            recorded_actions=tuple(self._actions),
            completed=True,
        )
        self._ledger.record_dry_run(record)
        return record


async def approve_activation(
    ledger: RoleVersionLedger, dry_run: DryRunRecord, *, approver: str, notes: str = ""
) -> ApprovalRecord:
    """DbC: require a completed shadow dry run on the current version and owner approval."""
    current = ledger.current
    if current is None:
        raise ActivationError("activation refused: role has no published version")
    normalized = approver.strip().lower() if isinstance(approver, str) else ""
    if normalized not in _APPROVED_ACTIVATION_PRINCIPALS:
        raise ActivationError("activation refused: requires recorded approval by dieter")
    if not dry_run.completed or dry_run.role_id != ledger.role_id or dry_run.version_id != current.version_id:
        raise ActivationError("activation refused: dry-run record does not match current version")
    if dry_run.content_hash != current.content_hash or any(not a.refused for a in dry_run.recorded_actions):
        raise ActivationError("activation refused: dry-run integrity check failed")

    approval = ApprovalRecord(
        ledger.role_id, current.version_id, current.content_hash, dry_run.run_id, normalized, notes.strip()
    )
    ledger.activate_current(approval)
    await append_orchestration_audit(
        {
            "orchestration_type": "bot_builder",
            "action": "bot_builder.activate",
            "role_id": ledger.role_id,
            "version_id": current.version_id,
            "content_hash": current.content_hash,
            "dry_run_id": dry_run.run_id,
            "recorded_action_count": len(dry_run.recorded_actions),
            "approver": normalized,
        },
    )
    return approval
