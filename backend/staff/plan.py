"""Staff run planning and request contracts.

Precondition: Requests and plans are structured dataclasses with explicit LoD boundaries.
"""

from __future__ import annotations

import re
from dataclasses import MISSING, dataclass, field, fields, replace
from typing import Any
from unittest.mock import MagicMock, Mock

from staff import consolidation
from staff.model_tiers import validate_role_tier

_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


@dataclass(frozen=True)
class RunRequest:
    """Validated, flat run request (built by the router from the POST body)."""

    role: str
    provider: str | None = None
    model: str | None = None
    # Internal dispatch intent; not a public HTTP request field.
    model_tier: str | None = None
    repo: str = ""
    issue: int | None = None
    pr: int | None = None
    prompt: str = ""
    machine: str = "local"
    requested_by: str = ""
    on_behalf_of: str = ""
    thread_id: str = ""
    work_item_id: str = ""
    # PR-consolidation decision from ``staff.consolidation.decide`` (#1213); None when not applicable.
    consolidation: dict[str, Any] | None = None
    origin_node: str = ""
    pr_lifecycle: str = "arm_and_exit"
    # Explicit operator PR restrictions (RM#2712); resolved by ``staff.lifecycle_fence``.
    pr_restrictions: tuple[str, ...] = ()
    # Existing branch the worktree starts from instead of origin/main (RD-1 CI fixes, #1881).
    head_ref: str = ""
    task_type: str = ""
    standards_extra: str = ""
    # When set, must exist in the skills registry (#2157); omit to use role/repo matching + fallback.
    requested_skill: str | None = None
    execution_mode: str = "local"
    dispatch_origin: str = ""
    # Barb lane dispatch: skip per-role repo allowlist when planning (#4117).
    barb_bypass_role_repos: bool = False

    def __post_init__(self) -> None:
        if self.head_ref and (not _SAFE_BRANCH.match(self.head_ref) or ".." in self.head_ref or not self.repo):
            raise ValueError("head_ref must be a safe branch name of the target repo")
        tier_errors = validate_role_tier(self.model_tier)
        if tier_errors:
            raise ValueError(f"model_tier {tier_errors[0]}")
        if self.model_tier is not None and self.model_tier.strip():
            object.__setattr__(self, "model_tier", self.model_tier.strip().lower())

    @property
    def target_kind(self) -> str:
        return "issue" if self.issue else ("pr" if self.pr else "prompt")

    @property
    def target_ref(self) -> str:
        return f"#{self.issue}" if self.issue else (f"PR #{self.pr}" if self.pr else "")


@dataclass(frozen=True)
class RunPlan:
    """What a run *would* do; returned by dry runs and used by the worker."""

    role: str
    provider: str
    model: str | None
    repo: str
    target_kind: str
    target_ref: str
    operator_prompt: str
    prompt: str
    argv: list[str]
    branch: str
    lease_ritual: bool
    consolidation: dict[str, Any] | None = None
    focus: str = ""  # board priorities + directives for this repo (#1239)
    thread_id: str = ""
    work_item_id: str = ""
    origin_node: str = ""
    pr_lifecycle: str = "arm_and_exit"
    # Operator PR restrictions behind ``pr_lifecycle`` (RM#2712); re-resolved at execute time
    # so the fence is re-derived (not just stored text) and still fails closed (#4156).
    pr_restrictions: tuple[str, ...] = ()
    head_ref: str = ""
    model_tier: str = ""
    skill_pin: str = ""
    standards_version: str = ""
    standards_tokens: int = 0
    context_stamp: str = "{}"
    library_work_ids: tuple[int, ...] = ()
    provider_auth_skipped: tuple[dict[str, str], ...] = ()
    injected_lesson_ids: tuple[str, ...] = ()
    skipped_lesson_ids: dict[str, str] = field(default_factory=dict)
    lessons_tokens: int = 0
    execution_mode: str = "local"
    cloud_stdin: str = ""

    @property
    def strategy_mode(self) -> str:
        return str(self.consolidation.get("mode", "")) if self.consolidation else ""

    @property
    def consolidation_paragraph(self) -> str:
        return consolidation.prompt_paragraph(self.consolidation) if self.consolidation else ""

    @property
    def issue_number(self) -> str:
        return self.target_ref.lstrip("#") if self.target_kind == "issue" else ""

    def derive(self, **overrides: Any) -> RunPlan:
        """Copy this plan, applying only the fields that change on retry/failover (#4259)."""
        return derive_run_plan(self, **overrides)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "model_tier": self.model_tier,
            "repo": self.repo,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "prompt": self.prompt,
            "argv": list(self.argv),
            "branch": self.branch,
            "lease_ritual": self.lease_ritual,
            "focus": self.focus,
            "thread_id": self.thread_id,
            "work_item_id": self.work_item_id,
            "pr_lifecycle": self.pr_lifecycle,
            "head_ref": self.head_ref,
            "consolidation": dict(self.consolidation) if self.consolidation else None,
            "skill_pin": self.skill_pin,
            "skill": None if not self.skill_pin else {"pin": self.skill_pin},
            "provider_auth_skipped": [dict(row) for row in self.provider_auth_skipped],
            "injected_lesson_ids": list(self.injected_lesson_ids),
            "skipped_lesson_ids": dict(self.skipped_lesson_ids),
            "lessons_tokens": self.lessons_tokens,
            "execution_mode": self.execution_mode,
        }


def _run_plan_field_default(fld: Any) -> Any:
    if fld.default is not MISSING:
        return fld.default
    if fld.default_factory is not MISSING:
        return fld.default_factory()
    msg = f"RunPlan field {fld.name!r} has no default"
    raise TypeError(msg)


def _is_unset_mock(value: object) -> bool:
    return isinstance(value, (MagicMock, Mock))


def derive_run_plan(plan: object, **overrides: Any) -> RunPlan:
    """Derive a :class:`RunPlan` from a plan or any legacy plan-shaped caller object (#4259)."""
    if isinstance(plan, RunPlan):
        return replace(plan, **overrides)
    values: dict[str, Any] = {}
    for fld in fields(RunPlan):
        if fld.name in overrides:
            continue
        if hasattr(plan, fld.name):
            val = getattr(plan, fld.name)
            values[fld.name] = _run_plan_field_default(fld) if _is_unset_mock(val) else val
        else:
            values[fld.name] = _run_plan_field_default(fld)
    values.update(overrides)
    argv = values.get("argv")
    if not isinstance(argv, list):
        values["argv"] = list(argv or [])
    return RunPlan(**values)
