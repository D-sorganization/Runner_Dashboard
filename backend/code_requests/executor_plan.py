"""Build the executor's children from the filed plan (WP-2.2, #1606).

The executor no longer takes its children from a caller: they come from the Code
Request's planning session, whose draft was approved and whose children were filed
as GitHub issues.
"""

from __future__ import annotations

from code_requests.executor_models import ChildIssuePayload, ExecutorTier
from code_requests.plan import PlanChild
from code_requests.planner import PlanningSession, PlanningStatus


class PlanNotFiledError(ValueError):
    """The planning session cannot seed an executor: no plan, not filed, or a child without an issue."""


def _tier(label: str) -> ExecutorTier:
    """``tier:cli`` (the plan's label form) or ``cli`` -> :class:`ExecutorTier`."""
    return ExecutorTier(label.removeprefix("tier:"))


def _payload(child: PlanChild, repository: str, issue_number: int) -> ChildIssuePayload:
    return ChildIssuePayload(
        key=child.key,
        title=child.title,
        repository=repository,
        issue_number=issue_number,
        dependencies=list(child.dependencies),
        tier=_tier(child.tier),
        task_class=child.task_class,
        complexity=child.complexity,
        file_scope=list(child.file_scope.allowed),
        acceptance_criteria=list(child.acceptance_criteria),
    )


def children_from_plan(session: PlanningSession | None, *, repository: str) -> list[ChildIssuePayload]:
    """The executor children of a filed plan, in plan order.

    Pre: ``session`` is ``filed``, has a draft, and every draft child has a filed issue number.
    Post: one payload per draft child, carrying its issue number, tier, scope and criteria.
    Raises :class:`PlanNotFiledError` when a precondition fails.
    """
    if session is None or session.draft is None:
        raise PlanNotFiledError("the Code Request has no plan")
    if session.status is not PlanningStatus.FILED:
        raise PlanNotFiledError(f"the plan is not filed (status {session.status.value!r})")
    filed = session.filing.children
    missing = [c.key for c in session.draft.children if c.key not in filed]
    if missing:
        raise PlanNotFiledError(f"plan children {', '.join(repr(k) for k in missing)} have no filed issue")
    return [_payload(c, repository, filed[c.key]) for c in session.draft.children]
