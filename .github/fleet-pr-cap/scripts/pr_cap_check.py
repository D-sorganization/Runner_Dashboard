"""Pure policy helpers for the fleet open pull-request cap."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, cast

from shared_scripts.agent_identity import (
    normalize_pr_agent_id,
    pr_author,
    pr_head_branch,
    pr_repository,
    resolve_pr_agent,
)

PR_CAP = 3
Verdict = Literal["PASS", "WARN", "FAIL"]
ReportStatus = Literal["PASS", "WARN"]
_AGENT_ID_RE = re.compile(r"^[a-z0-9-]+$")
_REPORT_REASONS = {
    "within_cap",
    "cap_exceeded",
    "unknown_identity",
    "current_closed_draft_or_exempt",
    "api_error",
    "api_timeout",
    "invalid_metadata",
    "policy_warn",
}


class CapReport(TypedDict):
    """Sanitized, machine-readable fields for a cap report."""

    status: ReportStatus
    reason: str
    agent: str | None
    count: int
    cap: int
    matching_numbers: list[int]
    unknown_count: int
    eligible_count: int


def cap_report(
    *,
    status: ReportStatus,
    reason: str,
    agent: str | None,
    count: int,
    cap: int,
    matching_numbers: list[int],
    unknown_count: int,
    eligible_count: int,
) -> CapReport:
    """Build the stable report object shared by text and JSON output."""
    return {
        "status": status,
        "reason": reason,
        "agent": agent,
        "count": count,
        "cap": cap,
        "matching_numbers": matching_numbers,
        "unknown_count": unknown_count,
        "eligible_count": eligible_count,
    }


def warn_report(cap: int, reason: str) -> CapReport:
    """Build a fail-open warning when a report cannot be evaluated."""
    return cap_report(
        status="WARN",
        reason=reason,
        agent=None,
        count=0,
        cap=cap,
        matching_numbers=[],
        unknown_count=0,
        eligible_count=0,
    )


def render_summary(report: Mapping[str, object]) -> str:
    """Render only validated report fields for a workflow summary."""
    fields = _validated_report_fields(report)
    if fields is None:
        return "WARN: Invalid PR-cap report data."
    status, reason, agent, count, cap, numbers, unknown_count, eligible_count = fields
    if reason == "api_error":
        return "WARN: GitHub API request failed; cap was not evaluated."
    if reason == "api_timeout":
        return "WARN: GitHub API request timed out; cap was not evaluated."
    if reason == "invalid_metadata":
        return (
            "WARN: GitHub returned incomplete or invalid pull-request metadata; "
            "cap was not evaluated."
        )
    if reason == "current_closed_draft_or_exempt":
        heading = "PASS: current PR is closed, draft, or exempt"
    elif reason == "unknown_identity":
        heading = "WARN: current PR identity is unknown"
    else:
        identity = agent or "unknown"
        refs = ", ".join(f"#{number}" for number in numbers) or "none"
        heading = (
            f"{status}: {count}/{cap} eligible open PRs for {identity}; "
            f"matching PRs {refs}"
        )
    percentage = unknown_count / eligible_count * 100 if eligible_count else 0.0
    return (
        f"{heading}. Unknown identity share: {unknown_count}/{eligible_count} "
        f"({percentage:.1f}%)."
    )


def _validated_report_fields(
    report: Mapping[str, object],
) -> tuple[str, str, str | None, int, int, list[int], int, int] | None:
    status = report.get("status")
    reason = report.get("reason")
    agent = report.get("agent")
    count = report.get("count")
    cap = report.get("cap")
    numbers = report.get("matching_numbers")
    unknown_count = report.get("unknown_count")
    eligible_count = report.get("eligible_count")
    if not isinstance(status, str) or status not in {"PASS", "WARN"}:
        return None
    if not isinstance(reason, str) or reason not in _REPORT_REASONS:
        return None
    if agent is not None and (
        not isinstance(agent, str) or not _AGENT_ID_RE.fullmatch(agent)
    ):
        return None
    integer_values = (count, cap, unknown_count, eligible_count)
    if any(type(value) is not int or value < 0 for value in integer_values):
        return None
    if not isinstance(numbers, list) or any(
        type(number) is not int or number <= 0 for number in numbers
    ):
        return None
    if numbers != sorted(set(numbers)):
        return None
    valid_count = cast(int, count)
    valid_cap = cast(int, cap)
    valid_unknown_count = cast(int, unknown_count)
    valid_eligible_count = cast(int, eligible_count)
    valid_numbers = cast(list[int], numbers)
    valid_agent = agent
    if valid_unknown_count > valid_eligible_count or valid_count > valid_eligible_count:
        return None
    if valid_count != len(valid_numbers) or (
        valid_agent is None and (valid_count or valid_numbers)
    ):
        return None
    return (
        status,
        reason,
        valid_agent,
        valid_count,
        valid_cap,
        valid_numbers,
        valid_unknown_count,
        valid_eligible_count,
    )


def count_open(prs: Sequence[Mapping[str, object]], agent: str) -> int:
    """Count eligible open PRs for ``agent`` in one repository only.

    Callers may pass metadata-light slices when they already obtained the PRs
    from a repository-scoped endpoint. Known target repositories are checked
    and mixed-repository inputs raise ``ValueError``.
    """
    target_agent = normalize_pr_agent_id(agent)
    return sum(
        1 for pr in eligible_open_prs(prs) if resolve_pr_agent(pr) == target_agent
    )


def eligible_open_prs(
    prs: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Return open PRs included in the per-agent cap denominator."""
    if len(_known_repositories(prs)) > 1:
        raise ValueError("prs must be scoped to a single repository")
    return [pr for pr in prs if _is_open(pr) and not _is_exempt(pr)]


def verdict(
    prs: Sequence[Mapping[str, object]],
    new_pr: Mapping[str, object],
    cap: int = PR_CAP,
) -> Verdict:
    """Return the cap decision for ``new_pr`` against current open PRs."""
    if type(cap) is not int or cap < 0:
        raise ValueError("cap must be a non-negative integer")
    if not _is_open(new_pr):
        return "PASS"
    if _is_exempt(new_pr):
        return "PASS"

    if len(_known_repositories((*prs, new_pr))) > 1:
        return "WARN"

    agent = resolve_pr_agent(new_pr)
    if agent is None or agent == "user":
        return "WARN"

    current_count = count_open(prs, agent)
    if not any(_same_pr(pr, new_pr) for pr in prs):
        current_count += 1
    return "FAIL" if current_count > cap else "PASS"


def _is_exempt(pr: Mapping[str, object]) -> bool:
    """Return whether policy excludes this PR from the agent cap."""
    if (
        _is_draft(pr)
        or _is_dependabot(pr)
        or _is_snapshot_branch(pr)
        or _is_ship_alone(pr)
    ):
        return True
    return False


def _is_draft(pr: Mapping[str, object]) -> bool:
    """Recognize GitHub CLI and API draft field names."""
    return bool(pr.get("isDraft", pr.get("draft", False)))


def _is_open(pr: Mapping[str, object]) -> bool:
    """Ignore explicitly closed PRs while accepting open-list response slices."""
    is_open = pr.get("isOpen")
    if isinstance(is_open, bool):
        return is_open
    state = pr.get("state")
    return not isinstance(state, str) or state.casefold() == "open"


def _is_dependabot(pr: Mapping[str, object]) -> bool:
    """Exclude dependabot-authored PRs from agent accounting."""
    login, _is_bot = pr_author(pr)
    return isinstance(login, str) and login.casefold() == "dependabot[bot]"


def _is_snapshot_branch(pr: Mapping[str, object]) -> bool:
    """Exclude fleet rule-sync and change-collation bot snapshots."""
    branch = pr_head_branch(pr)
    return branch.startswith(("bot/sync-fleet-agents", "bot/collate-changes"))


def _is_ship_alone(pr: Mapping[str, object]) -> bool:
    """Recognize the explicit policy label for PRs that ship alone."""
    labels = pr.get("labels")
    if not isinstance(labels, (list, tuple)):
        return False
    for label in labels:
        name = label.get("name") if isinstance(label, Mapping) else label
        if isinstance(name, str) and name.casefold() == "ship-alone":
            return True
    return False


def _same_pr(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    """Compare PR number and known target repository when available."""
    left_repository = pr_repository(left)
    right_repository = pr_repository(right)
    if (
        left_repository is not None
        and right_repository is not None
        and left_repository.casefold() != right_repository.casefold()
    ):
        return False
    left_number = left.get("number")
    right_number = right.get("number")
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return left == right


def _known_repositories(prs: Sequence[Mapping[str, object]]) -> set[str]:
    """Return case-insensitive known target repositories in the input."""
    return {
        repository.casefold()
        for pr in prs
        if (repository := pr_repository(pr)) is not None
    }
