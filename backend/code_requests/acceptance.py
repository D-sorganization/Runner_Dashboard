"""The acceptance check that gates ``executing -> done`` for a Code Request (WP-2.5, #1605).

A request is done only when every child is merged, its pull request is confirmed merged with
green head CI, and every acceptance criterion's latest recorded check passed. The PR verdict
reuses ``staff.verification.decide`` so runs and Code Requests judge a PR the same way. A PR
that cannot be looked up is an unmet condition, never a pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from code_requests.executor_models import AcceptanceCheck, ChildExecutionRecord, ChildExecutionState
from staff.verification import GitHubLookupError, PullRequest, decide

if TYPE_CHECKING:
    from collections.abc import Mapping


class PrNumberProbe(Protocol):
    def get(self, repo: str, number: int) -> PullRequest:
        """The PR ``number`` in ``repo`` with its head CI state. Raises GitHubLookupError."""


def record_check(child: ChildExecutionRecord, check: AcceptanceCheck) -> None:
    """Append ``check`` to ``child``. Precondition: it names one of the child's criteria."""
    if check.criterion not in child.acceptance_criteria:
        raise ValueError(f"{check.criterion!r} is not an acceptance criterion of child {child.key!r}")
    child.acceptance_checks.append(check)


def _pr_condition(child: ChildExecutionRecord, probe: PrNumberProbe) -> str | None:
    if child.state != ChildExecutionState.MERGED:
        return f"is {child.state.value}, not merged"
    if child.pr_number is None:
        return "has no pull request"
    try:
        pr = probe.get(child.repository, child.pr_number)
    except GitHubLookupError as exc:
        return f"PR #{child.pr_number} could not be verified: {exc}"
    verdict = decide(claimed="succeeded", opens_pr=True, branch="", pr=pr)
    if verdict.verification != "verified":
        return verdict.detail
    if pr.state != "merged":
        return f"PR #{pr.number} is {pr.state}, not merged"
    return None


def _criteria_conditions(child: ChildExecutionRecord) -> list[str]:
    latest = {check.criterion: check for check in child.acceptance_checks}
    unmet = []
    for criterion in child.acceptance_criteria:
        check = latest.get(criterion)
        if check is None:
            unmet.append(f"no recorded acceptance check for {criterion!r}")
        elif not check.passed:
            unmet.append(f"acceptance check failed for {criterion!r}")
    return unmet


def unmet_conditions(children: Mapping[str, ChildExecutionRecord], probe: PrNumberProbe) -> list[str]:
    """Every reason the request may not enter ``done``; empty means the acceptance gate is met."""
    if not children:
        return ["the pipeline has no children"]
    unmet: list[str] = []
    for key, child in children.items():
        pr_problem = _pr_condition(child, probe)
        if pr_problem is not None:
            unmet.append(f"child {key!r} {pr_problem}")
        unmet.extend(f"child {key!r}: {reason}" for reason in _criteria_conditions(child))
    return unmet
