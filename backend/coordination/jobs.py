"""Fleet Job Board S1: read-only claimable-work listing (issue #2782, epic #2781).

Reuses the manual claim ritual's evidence (``coordination.claims``, #2765's
``staff.claim_guard.closing_pr_for``) instead of a second claim store. Holds (S2)
and claim/release (S3) are later slices; this module only lists, never writes.
"""

from __future__ import annotations

import importlib
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from activity_producers import record_job_claim_activity
from coordination import board as board_mod
from coordination import claim_state as claim_state_mod
from coordination import claims as claims_mod
from coordination import claims_check_cache, gh_cli, job_board_lease
from coordination.board import RMScriptError
from coordination.claims_check_cache import ClaimCheckUnavailable
from coordination.job_filters import gate_reason, repo_schedule_holds
from coordination.merge_shepherd_holds import MERGE_SHEPHERD_HOLD_LABELS
from coordination.pr_age_rule import claim_blocked_by_aging_prs
from staff.claim_guard import closing_pr_for, full_repo
from staff.launch_guard import check_launch_allowed

log = logging.getLogger("dashboard.coordination.jobs")

MIN_LIMIT = 1
MAX_LIMIT = 200
CLOSING_PR_PULLS_PER_PAGE = 100
# Job-board claiming uses the full merge-shepherd hold set (not LOW_RISK_SHEPHERD_BLOCKED_LABELS).
HOLD_LABELS = MERGE_SHEPHERD_HOLD_LABELS
PRIORITY_TIERS = ("critical", "high", "medium", "low")
_UNRANKED = len(PRIORITY_TIERS)


def _safe_top_priorities(limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    """``priorities.service.top_priorities(limit)``; a missing/failing sibling is a warning, not a 500."""
    try:
        module = importlib.import_module("priorities.service")
    except ImportError:
        return [], []
    loader = getattr(module, "top_priorities", None)
    if loader is None:
        return [], []
    try:
        return list(loader(limit) or []), []
    except Exception as exc:  # noqa: BLE001
        log.warning("jobs: priorities unavailable: %s", exc)
        return [], [f"priorities unavailable: {exc}"]


def _label_names(issue: dict[str, Any]) -> list[str]:
    names = [(lbl.get("name") if isinstance(lbl, dict) else str(lbl)) for lbl in issue.get("labels") or []]
    return [n for n in names if n]


def _priority_tier_and_name(lowered_labels: set[str]) -> tuple[int, str]:
    for tier, name in enumerate(PRIORITY_TIERS):
        if name in lowered_labels:
            return tier, name
    return _UNRANKED, ""


def _board_rank(priorities: list[dict[str, Any]], repo: str, issue_number: int) -> int:
    """Position of the first priority item naming ``repo#issue_number``, else past the end (unranked)."""
    needle = f"#{issue_number}"
    for index, item in enumerate(priorities):
        haystack = " ".join(str(item.get(k) or "") for k in ("item", "scope", "tracking", "project"))
        if needle in haystack and repo.casefold() in haystack.casefold():
            return index
    return len(priorities)


def _excluded(repo: str, issue: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "repo": repo,
        "issue": issue.get("number"),
        "title": str(issue.get("title") or ""),
        "url": str(issue.get("html_url") or ""),
        "reason": reason,
    }


def bounded_open_pulls_path(repo_full: str) -> str:
    """GitHub path for one recency-sorted page of open PRs (closing-evidence lookup).

    Pre: ``repo_full`` is ``owner/name``.
    Post: path requests at most ``CLOSING_PR_PULLS_PER_PAGE`` open PRs, newest-updated first.
    """
    if repo_full.count("/") != 1:
        raise ValueError("repo_full must be owner/name")
    return f"/repos/{repo_full}/pulls?state=open&sort=updated&direction=desc&per_page={CLOSING_PR_PULLS_PER_PAGE}"


def closing_pr_search_path(repo_full: str, issue_number: int) -> str:
    """One GitHub search request for open PRs whose title/body references ``#{issue_number}``.

    Pre: ``repo_full`` is ``owner/name``; ``issue_number`` is positive.
    Post: returns a ``/search/issues`` path with ``per_page`` capped at 10.
    """
    if repo_full.count("/") != 1 or int(issue_number) <= 0:
        raise ValueError("closing_pr_search_path needs owner/name and a positive issue number")
    query = quote(f'repo:{repo_full} is:pr is:open "#{issue_number}" in:title,body', safe="")
    return f"/search/issues?q={query}&per_page=10"


def _search_open_closing_pr(repo_full: str, issue_number: int) -> dict[str, Any] | None:
    """Return closing evidence; only a complete, fully examined search proves absence.

    Inconclusive evidence raises so ``list_jobs`` excludes the issue and marks
    completeness false. Never paginate searches to resolve uncertainty.
    """
    raw = gh_cli.gh_api_json(closing_pr_search_path(repo_full, issue_number))
    if not isinstance(raw, dict) or raw.get("incomplete_results") is not False:
        raise ValueError("closing PR search is incomplete or malformed")
    items = raw.get("items")
    total = raw.get("total_count")
    if not isinstance(items, list) or type(total) is not int or total < 0 or total != len(items):
        raise ValueError("closing PR search has malformed or unexamined matches")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("title"), str)
        or (item.get("body") is not None and not isinstance(item["body"], str))
        for item in items
    ):
        raise ValueError("closing PR search returned malformed matches")
    return closing_pr_for(items, issue_number)


def _open_pulls_page(
    repo_full: str,
    pulls_by_repo: dict[str, list[Any]],
    truncated_by_repo: dict[str, bool],
) -> list[Any]:
    """Load (or return cached) one page of open pulls; record truncation when the page is full."""
    cached = pulls_by_repo.get(repo_full)
    if cached is not None:
        return cached
    raw = gh_cli.gh_api_json(bounded_open_pulls_path(repo_full))
    pulls = raw if isinstance(raw, list) else []
    pulls_by_repo[repo_full] = pulls
    truncated_by_repo[repo_full] = len(pulls) >= CLOSING_PR_PULLS_PER_PAGE
    return pulls


def _closing_pr(
    repo_full: str,
    issue_number: int,
    pulls_by_repo: dict[str, list[Any]],
    *,
    truncated_by_repo: dict[str, bool] | None = None,
) -> dict[str, Any] | None:
    """An open PR closing ``issue_number``; bounded page plus search when truncated (#4622, #4627)."""
    trunc = truncated_by_repo if truncated_by_repo is not None else {}
    pulls = _open_pulls_page(repo_full, pulls_by_repo, trunc)
    hit = closing_pr_for(pulls, issue_number)
    if hit is not None:
        return hit
    if trunc.get(repo_full):
        return _search_open_closing_pr(repo_full, issue_number)
    return None


def _candidates(repo: str, warnings: list[str]) -> tuple[list[dict[str, Any]], bool]:
    """Open issues (PRs excluded) for ``repo``; ``(issues, repo_complete)``."""
    repo_full = full_repo(repo)
    try:
        raw = gh_cli.gh_api_json_all(f"/repos/{repo_full}/issues?state=open")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{repo}: issue list unavailable ({exc})")
        return [], False
    if not isinstance(raw, list):
        warnings.append(f"{repo}: issue list returned an invalid payload")
        return [], False
    return [issue for issue in raw if isinstance(issue, dict) and "pull_request" not in issue], True


def _list_claim_status(repo: str, number: int, deadline: float | None) -> dict[str, Any]:
    """Claim status for one listing row.

    Without a deadline this is the direct check existing callers already use.
    With a deadline the shared claim cache must answer inside the time left;
    a miss is unavailable and tells the listing to stop, never to treat the
    issue as free.
    """
    if deadline is None:
        return claims_mod.check(repo, number)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {
            "available": False,
            "reason": "job-board response budget exhausted",
            "held": None,
            "_budget_exhausted": True,
        }
    try:
        return claims_check_cache.read(repo, number, budget_seconds=remaining)
    except ClaimCheckUnavailable as exc:
        return {"available": False, "reason": str(exc), "held": None, "_budget_exhausted": True}


def list_jobs(
    *,
    repo: str | None,
    limit: int,
    include_excluded: bool,
    budget_seconds: float | None = None,
) -> dict[str, Any]:
    """``{available, complete, jobs, excluded?, warnings, generated_at}``; every ``jobs`` entry is open,
    unclaimed and unheld (an unknown lease state excludes it with a warning — unknown is never free)."""
    assert MIN_LIMIT <= limit <= MAX_LIMIT  # noqa: S101 — contract boundary, route already validates
    deadline = None if budget_seconds is None else time.monotonic() + max(0.0, float(budget_seconds))
    repos = [repo] if repo else board_mod.fleet_repos()
    warnings: list[str] = []
    jobs: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    pulls_by_repo: dict[str, list[Any]] = {}
    pulls_truncated_by_repo: dict[str, bool] = {}
    pull_errors: dict[str, str] = {}
    truncation_warnings: set[str] = set()
    complete = True
    holds_available = True
    priorities, priority_warnings = _safe_top_priorities(50)
    warnings.extend(priority_warnings)
    budget_stop = ""
    for one_repo in repos:
        if deadline is not None and time.monotonic() >= deadline:
            complete = False
            budget_stop = f"{one_repo}: stopped at job-board response budget"
            break
        issues, repo_complete = _candidates(one_repo, warnings)
        complete = complete and repo_complete
        repo_full = full_repo(one_repo)
        scoped_holds, holds_readable = repo_schedule_holds(one_repo)
        if not holds_readable:
            holds_available = False
            warnings.append(f"{one_repo}: holds list unreadable, excluding repo for safety")
        for issue in issues:
            if deadline is not None and time.monotonic() >= deadline:
                complete = False
                budget_stop = f"{one_repo}: stopped at job-board response budget before claim checks finished"
                break
            number = issue.get("number")
            if not isinstance(number, int):
                continue
            labels = _label_names(issue)
            lowered = {name.strip().lower() for name in labels}
            if not holds_readable:
                excluded.append(_excluded(one_repo, issue, "holds list unreadable"))
                continue
            claim_label = next((name for name in labels if name.lower().startswith("claim:")), None)
            if claim_label:
                excluded.append(_excluded(one_repo, issue, f"carries {claim_label}"))
                continue
            hold_label = next((name for name in lowered if name in HOLD_LABELS), None)
            if hold_label:
                excluded.append(_excluded(one_repo, issue, f"carries hold label '{hold_label}'"))
                continue
            gate_or_hold_reason = gate_reason(lowered, scoped_holds)
            if gate_or_hold_reason:
                excluded.append(_excluded(one_repo, issue, gate_or_hold_reason))
                continue
            if repo_full in pull_errors:
                excluded.append(_excluded(one_repo, issue, f"PR lookup unavailable: {pull_errors[repo_full]}"))
                continue
            try:
                hit = _closing_pr(repo_full, number, pulls_by_repo, truncated_by_repo=pulls_truncated_by_repo)
                if pulls_truncated_by_repo.get(repo_full) and repo_full not in truncation_warnings:
                    truncation_warnings.add(repo_full)
                    warnings.append(
                        f"{one_repo}: open PR list capped at {CLOSING_PR_PULLS_PER_PAGE}; "
                        "targeted search used for closing evidence beyond the first page"
                    )
            except Exception as exc:  # noqa: BLE001
                pull_errors[repo_full] = str(exc)
                complete = False
                excluded.append(_excluded(one_repo, issue, f"PR lookup unavailable: {exc}"))
                warnings.append(f"{one_repo}#{number}: PR lookup unavailable ({exc})")
                continue
            if hit is not None:
                excluded.append(_excluded(one_repo, issue, f"open PR #{hit.get('number')} already closes it"))
                continue
            status = _list_claim_status(one_repo, number, deadline)
            if status.get("_budget_exhausted"):
                complete = False
                budget_stop = f"{one_repo}#{number}: claim check exceeded job-board response budget"
                excluded.append(_excluded(one_repo, issue, "claim check exceeded job-board response budget"))
                break
            if not status["available"]:
                excluded.append(_excluded(one_repo, issue, f"claim check unavailable: {status['reason']}"))
                warnings.append(f"{one_repo}#{number}: claim check unavailable ({status['reason']})")
                continue
            if status["held"]:
                excluded.append(_excluded(one_repo, issue, f"claimed by {status.get('agent') or 'another agent'}"))
                continue
            tier, priority_name = _priority_tier_and_name(lowered)
            jobs.append(
                {
                    "repo": one_repo,
                    "issue": number,
                    "title": str(issue.get("title") or ""),
                    "url": str(issue.get("html_url") or ""),
                    "labels": labels,
                    "priority": priority_name,
                    "updated_at": str(issue.get("updated_at") or ""),
                    "_sort": (tier, _board_rank(priorities, one_repo, number), str(issue.get("created_at") or "")),
                }
            )
        if budget_stop:
            break
    if budget_stop:
        warnings.append(budget_stop)
    jobs.sort(key=lambda j: j["_sort"])
    for job in jobs:
        del job["_sort"]
    result: dict[str, Any] = {
        "available": holds_available,
        "complete": complete,
        "jobs": jobs[:limit],
        "warnings": warnings,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if include_excluded:
        result["excluded"] = excluded
    return result


class JobIneligibleError(RuntimeError):
    """Listed job is not claimable; mapped to HTTP 409."""

    def __init__(self, reason: str, *, held_by: str = "", expires_at: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.held_by = held_by
        self.expires_at = expires_at

    def to_detail(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "held_by": self.held_by,
            "reason": self.reason,
            "expires_at": self.expires_at,
            "guidance": "Pick another job from GET /api/coordination/jobs or refresh eligibility.",
        }


class StaleFenceError(RuntimeError):
    """Release/heartbeat fence does not match the current lease epoch."""

    def to_detail(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "held_by": "",
            "reason": "stale fence",
            "expires_at": None,
            "guidance": "Re-read GET /api/coordination/claims for the current fence.",
        }


def reset_job_board_claim_state() -> None:
    """Drop this process's handle on the durable claim state, as a restart does (tests).

    Fences and lease rows are durable (#4270 B-09), so they are still there afterwards;
    ``purge_job_board_claim_state`` deletes them.
    """
    claim_state_mod.reset_claim_state_store()


def purge_job_board_claim_state() -> None:
    """Delete every durable fence and lease row (tests)."""
    claim_state_mod.get_store().purge_job_board_rows()


def _mint_fence(repo: str, issue: int) -> int:
    return claim_state_mod.get_store().mint_fence(repo, issue)


def _current_fence(repo: str, issue: int) -> int:
    return claim_state_mod.get_store().current_fence(repo, issue)


def _require_fence(repo: str, issue: int, fence: int) -> None:
    if fence <= 0 or fence != _current_fence(repo, issue):
        raise StaleFenceError(f"fence {fence} is not current for {repo}#{issue}")


def _fetch_open_issue(repo: str, issue: int) -> dict[str, Any]:
    repo_full = full_repo(repo)
    raw = claims_mod.evidence_fetch(f"/repos/{repo_full}/issues/{issue}")
    if not isinstance(raw, dict) or raw.get("state") != "open" or "pull_request" in raw:
        raise JobIneligibleError(f"{repo}#{issue} is not an open issue")
    return raw


def _ineligible_reason(repo: str, issue: dict[str, Any], pulls_by_repo: dict[str, list[Any]]) -> str | None:
    number = issue.get("number")
    if not isinstance(number, int):
        return "invalid issue payload"
    labels = _label_names(issue)
    lowered = {name.strip().lower() for name in labels}
    scoped_holds, holds_readable = repo_schedule_holds(repo)
    if not holds_readable:
        return "holds list unreadable"
    claim_label = next((name for name in labels if name.lower().startswith("claim:")), None)
    if claim_label:
        return f"carries {claim_label}"
    hold_label = next((name for name in lowered if name in HOLD_LABELS), None)
    if hold_label:
        return f"carries hold label '{hold_label}'"
    gate_or_hold = gate_reason(lowered, scoped_holds)
    if gate_or_hold:
        return gate_or_hold
    repo_full = full_repo(repo)
    try:
        hit = _closing_pr(repo_full, number, pulls_by_repo)
    except Exception as exc:  # noqa: BLE001
        return f"PR lookup unavailable: {exc}"
    if hit is not None:
        return f"open PR #{hit.get('number')} already closes it"
    status = claims_mod.check(repo, number)
    if not status["available"]:
        raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
    if status["held"]:
        return f"claimed by {status.get('agent') or 'another agent'}"
    launch = check_launch_allowed(repo, number)
    if not launch.allowed:
        return launch.reason
    return None


def _assert_claimable(repo: str, issue: int) -> None:
    issue_payload = _fetch_open_issue(repo, issue)
    reason = _ineligible_reason(repo, issue_payload, {})
    if reason:
        raise JobIneligibleError(reason)


def _undo_unrecorded_claim(repo: str, issue: int, *, agent: str, session: str, holder_principal: str | None) -> None:
    """Release the lease just posted when its fence or lease row could not be stored (#4270 B-09).

    Without the fence the holder can neither heartbeat nor release, and without the row
    the sweeper never sees the lease. Best effort: the caller re-raises the store error.
    """
    undo = (
        lambda: claims_mod.release(
            repo, issue, agent=agent, session=session, reason="claim not recorded", holder_principal=holder_principal
        ),
        lambda: job_board_lease.drop_lease(repo, issue),
        lambda: claim_state_mod.get_store().abandon_acquire(repo, issue, agent=agent, session=session),
    )
    for step in undo:
        try:
            step()
        except Exception:  # noqa: BLE001 — every step is tried; the original error is what the caller sees
            log.error("jobs: could not undo the unrecorded claim of %s#%s", repo, issue, exc_info=True)


def claim_job(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    intent: str,
    holder_principal: str | None = None,
    ttl_hours: float = job_board_lease.DEFAULT_TTL_HOURS,
) -> dict[str, Any]:
    """Atomic job-board claim with monotonic fencing (#2784)."""
    assert agent and session and intent  # noqa: S101 — route validates
    job_board_lease.validate_ttl(ttl_hours)
    with claims_mod.hold_issue(repo, issue):
        _assert_claimable(repo, issue)
        status = claims_mod.check(repo, issue)
        if not status["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
        if status["held"]:
            raise claims_mod.ClaimHeldError(status)
        aging_block = claim_blocked_by_aging_prs(agent, claim_repo=repo, fetch=claims_mod.evidence_fetch)
        if aging_block is not None:
            raise claims_mod.AgingPrBlockedError(aging_block)
        cas = claim_state_mod.acquire_compare_and_set(repo, issue, agent=agent, session=session)
        if not cas.acquired:
            raise claims_mod.ClaimHeldError(claim_state_mod.holder_status_from_row(repo, issue, cas))
        try:
            posted = claims_mod.post_lease(
                repo, issue, agent, session, intent, ttl_hours=ttl_hours, rollback_orphan_label=True
            )
        except Exception as exc:
            claim_state_mod.keep_authoritative_claim(repo, issue, agent=agent, session=session, exc=exc)
            raise
        try:
            after = claims_mod.check(repo, issue)
            if not after["available"]:
                raise RMScriptError(f"check_agent_claim unavailable after post_agent_lease: {after['reason']}")
            owned = bool(after["held"]) and claims_mod._is_own(after, agent, session)
            if after["held"] and not owned:
                try:
                    claims_mod.release(
                        repo,
                        issue,
                        agent=agent,
                        session=session,
                        reason="race loser",
                        holder_principal=holder_principal,
                    )
                except Exception:  # noqa: BLE001 — best-effort rollback; caller still gets 409
                    log.warning("jobs: race rollback release failed for %s#%s", repo, issue, exc_info=True)
                # fmt: off
                record_job_claim_activity(
                    kind="conflict",
                    repo=repo,
                    issue=issue,
                    agent=agent,
                    session=session,
                    holder_principal=holder_principal,
                    extra={"reason": "race loser"},
                )
                # fmt: on
                raise claims_mod.ClaimHeldError(after)
            if not owned:
                raise RMScriptError(
                    f"check_agent_claim could not verify the posted lease for {repo}#{issue} "
                    f"(held={after['held']!r}); treating the claim as unverified, not successful"
                )
        except Exception:
            claim_state_mod.get_store().abandon_acquire(repo, issue, agent=agent, session=session)
            raise
        lease = posted["result"]
        receipt = str(lease.get("receipt") or "")
        expires_at = str(lease.get("expires_at") or after.get("expires_at") or "")
        try:
            fence = _mint_fence(repo, issue)
            # fmt: off
            job_board_lease.remember_lease(
                repo, issue, agent=agent, session=session, fence=fence, ttl_hours=ttl_hours,
                expires_at=expires_at or None,
            )
            # fmt: on
        except Exception:
            _undo_unrecorded_claim(repo, issue, agent=agent, session=session, holder_principal=holder_principal)
            raise
        # fmt: off
        record_job_claim_activity(
            kind="claim",
            repo=repo,
            issue=issue,
            agent=agent,
            session=session,
            holder_principal=holder_principal,
            receipt=receipt,
            fencing_token=str(fence),
            extra={"intent": intent},
        )
        # fmt: on
        principal = holder_principal or f"agent-{agent}"
        return {
            "claimed": True,
            "repo": repo,
            "issue": issue,
            "agent": agent,
            "principal": principal,
            "session": session,
            "expires_at": expires_at or None,
            "fence": fence,
            "warnings": posted.get("warnings") or [],
        }


def release_job(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    fence: int,
    reason: str,
    holder_principal: str | None = None,
) -> dict[str, Any]:
    """Release a job-board claim; requires the current fence (#2784)."""
    _require_fence(repo, issue, fence)
    with claims_mod.hold_issue(repo, issue):
        status = claims_mod.check(repo, issue)
        if not status["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
        if not status.get("held") or (status.get("agent"), status.get("session")) != (agent, session):
            raise StaleFenceError(f"session {session!r} does not hold {repo}#{issue}")
        released = claims_mod.release(
            repo,
            issue,
            agent=agent,
            session=session,
            reason=reason,
            holder_principal=holder_principal,
            fencing_token=str(fence),
        )
        job_board_lease.drop_lease(repo, issue)
        return {
            "released": True,
            "repo": repo,
            "issue": issue,
            "fence": fence,
            "warnings": released.get("warnings") or [],
        }


# Re-export TTL helpers for routes, reaper, and tests.
heartbeat_job = job_board_lease.heartbeat_job
sweep_expired_job_claims = job_board_lease.sweep_expired_job_claims
DEFAULT_TTL_HOURS = job_board_lease.DEFAULT_TTL_HOURS
_remember_lease = job_board_lease.remember_lease
