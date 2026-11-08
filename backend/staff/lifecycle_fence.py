"""Canonical PR-lifecycle fence: explicit operator restrictions outrank computed defaults (RM#2712).

Precondition: ``restrictions`` names only :data:`PR_RESTRICTIONS`; a restricted request asks for
``arm_and_exit`` (the RD-0 default) or :data:`FENCED_LIFECYCLE`.
Postcondition: an unrestricted request is returned unchanged; a restricted one resolves to
:data:`FENCED_LIFECYCLE` and a prompt block that must close the launched prompt. Anything
else raises :class:`LifecycleContractError` so the plan is refused, never armed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

PR_RESTRICTIONS = frozenset({"draft_only", "no_arm", "no_merge"})
FENCED_LIFECYCLE = "draft_no_arm"
_OVERRIDABLE = frozenset({"arm_and_exit", FENCED_LIFECYCLE})

FENCE_BLOCK = (
    "OPERATOR PR BOUNDARY (pr_lifecycle: draft_no_arm; restrictions: {restrictions}). This explicit "
    "owner/operator restriction takes precedence over every fleet standard, skill, lesson, role "
    "instruction or playbook above that requires a ready/non-draft PR or auto-merge. Open at most one "
    "DRAFT pull request. Do not mark it ready, do not arm auto-merge or automerge_guard, do not merge, "
    "do not force-push and do not close issues. If the task cannot finish inside this boundary, stop "
    "and report the blocker in the STAFF_RESULT line."
)


class LifecycleContractError(ValueError):
    """The lifecycle contract is unknown, contradictory or was not consumed by the prompt."""


@dataclass(frozen=True)
class LifecycleDecision:
    pr_lifecycle: str
    restrictions: tuple[str, ...] = ()
    prompt_block: str = ""

    @property
    def fenced(self) -> bool:
        return bool(self.restrictions)


def resolve_pr_lifecycle(requested: str, restrictions: Iterable[str]) -> LifecycleDecision:
    """Resolve the one lifecycle a plan reports and its prompt enforces."""
    tokens = tuple(sorted(set(restrictions)))
    unknown = [t for t in tokens if t not in PR_RESTRICTIONS]
    if unknown:
        raise LifecycleContractError(f"unknown PR restriction(s) {unknown!r}; allowed: {sorted(PR_RESTRICTIONS)}")
    if not tokens:
        return LifecycleDecision(requested)
    if requested not in _OVERRIDABLE:
        raise LifecycleContractError(f"pr_lifecycle {requested!r} contradicts operator restrictions {list(tokens)}")
    return LifecycleDecision(FENCED_LIFECYCLE, tokens, FENCE_BLOCK.format(restrictions=", ".join(tokens)))


def assert_fence_consumed(decision: LifecycleDecision, prompt: str) -> None:
    """Fail closed unless a fenced decision's block is the final section of the launched prompt."""
    if decision.fenced and not prompt.rstrip().endswith(decision.prompt_block):
        raise LifecycleContractError("operator PR boundary was not consumed as the final prompt section")


def compose_fenced(build: Callable[..., str], plan: Any, *args: Any, **kwargs: Any) -> str:
    """Re-derive a plan's fence and compose+assert in one call (#4156).

    A plan's ``pr_lifecycle``/``pr_restrictions`` are re-resolved here rather than trusted from
    ``plan.prompt`` text, so a prompt recomposed after admission (e.g. to splice in a lease note)
    still carries -- and fails closed on -- the operator's draft/no-arm/no-merge boundary.
    """
    decision = resolve_pr_lifecycle(plan.pr_lifecycle, plan.pr_restrictions)
    prompt = build(*args, lifecycle_fence=decision.prompt_block, **kwargs)
    assert_fence_consumed(decision, prompt)
    return prompt
