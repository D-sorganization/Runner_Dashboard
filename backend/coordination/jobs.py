"""Fleet Job Board S1: read-only claimable-work listing (issue #2782, epic #2781).

Reuses the manual claim ritual's evidence (``coordination.claims``, #2765's
``staff.claim_guard.closing_pr_for``) instead of a second claim store. Holds (S2)
and claim/release (S3) are later slices; this module only lists, never writes.
"""

from __future__ import annotations

import importlib
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from activity_producers import record_job_claim_activity
from coordination import board as board_mod
from coordination import claims as claims_mod
from coordination.board import RMScriptError
from coordination.job_filters import gate_reason, repo_schedule_holds
from coordination.pr_age_rule import claim_blocked_by_aging_prs
from staff.claim_guard import closing_pr_for, full_repo
from staff.launch_guard import check_launch_allowed

log = logging.getLogger("dashboard.coordination.jobs")

MIN_LIMIT = 1
MAX_LIMIT = 200
HOLD_LABELS = frozenset({"blocked", "do-not-automate", "do-not-merge"})
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


def _closing_pr(repo_full: str, issue_number: int, pulls_by_repo: dict[str, list[Any]]) -> dict[str, Any] | None:
    """An open/merged PR closing ``issue_number``; one pulls fetch per repo."""
    pulls = pulls_by_repo.get(repo_full)
    if pulls is None:
        raw = claims_mod.evidence_fetch(f"/repos/{repo_full}/pulls?state=all&per_page=100")
        candidates = raw if isinstance(raw, list) else []
        pulls = [pr for pr in candidates if pr.get("state") != "closed" or pr.get("merged_at")]
        pulls_by_repo[repo_full] = pulls
    return closing_pr_for(pulls, issue_number)


def _candidates(repo: str, warnings: list[str]) -> tuple[list[dict[str, Any]], bool]:
    """Open issues (PRs excluded) for ``repo``; ``(issues, repo_complete)``."""
    repo_full = full_repo(repo)
    try:
        raw = claims_mod.evidence_fetch(f"/repos/{repo_full}/issues?state=open&per_page=100")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{repo}: issue list unavailable ({exc})")
        return [], False
    if not isinstance(raw, list):
        warnings.append(f"{repo}: issue list returned an invalid payload")
        return [], False
    return [issue for issue in raw if isinstance(issue, dict) and "pull_request" not in issue], True


def list_jobs(*, repo: str | None, limit: int, include_excluded: bool) -> dict[str, Any]:
    """``{available, complete, jobs, excluded?, warnings, generated_at}``; every ``jobs`` entry is open,
    unclaimed and unheld (an unknown lease state excludes it with a warning — unknown is never free)."""
    assert MIN_LIMIT <= limit <= MAX_LIMIT  # noqa: S101 — contract boundary, route already validates
    repos = [repo] if repo else board_mod.fleet_repos()
    warnings: list[str] = []
    jobs: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    pulls_by_repo: dict[str, list[Any]] = {}
    complete = True
    holds_available = True
    priorities, priority_warnings = _safe_top_priorities(50)
    warnings.extend(priority_warnings)
    for one_repo in repos:
        issues, repo_complete = _candidates(one_repo, warnings)
        complete = complete and repo_complete
        repo_full = full_repo(one_repo)
        scoped_holds, holds_readable = repo_schedule_holds(one_repo)
        if not holds_readable:
            holds_available = False
            warnings.append(f"{one_repo}: holds list unreadable, excluding repo for safety")
        for issue in issues:
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
            try:
                hit = _closing_pr(repo_full, number, pulls_by_repo)
            except Exception as exc:  # noqa: BLE001
                excluded.append(_excluded(one_repo, issue, f"PR lookup unavailable: {exc}"))
                warnings.append(f"{one_repo}#{number}: PR lookup unavailable ({exc})")
                continue
            if hit is not None:
                excluded.append(_excluded(one_repo, issue, f"open PR #{hit.get('number')} already closes it"))
                continue
            status = claims_mod.check(one_repo, number)
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


_fence_guard = threading.Lock()
_fences: dict[str, int] = {}
_lease_guard = threading.Lock()
_lease_state: dict[str, tuple[str, int, str, str, int, datetime, float]] = {}
DEFAULT_TTL_HOURS = 2.0
MIN_TTL_HOURS = 0.1
MAX_TTL_HOURS = 8.0


def reset_job_board_claim_state() -> None:
    """Forget in-process fencing and TTL bookkeeping (tests)."""
    with _fence_guard:
        _fences.clear()
    with _lease_guard:
        _lease_state.clear()


def _fence_key(repo: str, issue: int) -> str:
    return f"{repo.casefold()}#{issue}"


def _mint_fence(repo: str, issue: int) -> int:
    with _fence_guard:
        key = _fence_key(repo, issue)
        value = _fences.get(key, 0) + 1
        _fences[key] = value
        return value


def _current_fence(repo: str, issue: int) -> int:
    with _fence_guard:
        return _fences.get(_fence_key(repo, issue), 0)


def _require_fence(repo: str, issue: int, fence: int) -> None:
    if fence <= 0 or fence != _current_fence(repo, issue):
        raise StaleFenceError(f"fence {fence} is not current for {repo}#{issue}")


def _parse_expires(raw: str | None, *, ttl_hours: float) -> datetime:
    if raw:
        text = raw.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC) + timedelta(hours=ttl_hours)


def _remember_lease(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    fence: int,
    ttl_hours: float,
    expires_at: str | None,
) -> None:
    expires = _parse_expires(expires_at, ttl_hours=ttl_hours)
    with _lease_guard:
        _lease_state[_fence_key(repo, issue)] = (repo, issue, agent, session, fence, expires, ttl_hours)


def _drop_lease(repo: str, issue: int) -> None:
    with _lease_guard:
        _lease_state.pop(_fence_key(repo, issue), None)


def _validate_ttl(ttl_hours: float) -> None:
    if not MIN_TTL_HOURS <= ttl_hours <= MAX_TTL_HOURS:
        raise ValueError(f"ttl_hours must be between {MIN_TTL_HOURS} and {MAX_TTL_HOURS}")


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


def claim_job(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    intent: str,
    holder_principal: str | None = None,
    ttl_hours: float = DEFAULT_TTL_HOURS,
) -> dict[str, Any]:
    """Atomic job-board claim with monotonic fencing (#2784)."""
    assert agent and session and intent  # noqa: S101 — route validates
    _validate_ttl(ttl_hours)
    with claims_mod.hold_issue(repo, issue):
        pre = claims_mod.check(repo, issue)
        if not pre["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {pre['reason']}")
        if pre.get("held") and (pre.get("agent"), pre.get("session")) != (agent, session):
            record_job_claim_activity(
                kind="conflict",
                repo=repo,
                issue=issue,
                agent=agent,
                session=session,
                holder_principal=holder_principal,
                extra={
                    "challenger_principal": holder_principal or f"agent-{agent}",
                    "reason": str(pre.get("reason") or ""),
                },
            )
            raise claims_mod.ClaimHeldError(pre)
        _assert_claimable(repo, issue)
        status = claims_mod.check(repo, issue)
        if not status["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
        if status["held"]:
            raise claims_mod.ClaimHeldError(status)
        aging_block = claim_blocked_by_aging_prs(agent, claim_repo=repo, fetch=claims_mod.evidence_fetch)
        if aging_block is not None:
            raise claims_mod.AgingPrBlockedError(aging_block)
        posted = claims_mod.post_lease(repo, issue, agent, session, intent, ttl_hours=ttl_hours)
        after = claims_mod.check(repo, issue)
        if after.get("held") and (after.get("agent"), after.get("session")) != (agent, session):
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
        fence = _mint_fence(repo, issue)
        lease = posted["result"]
        receipt = str(lease.get("receipt") or "")
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
        expires_at = str(lease.get("expires_at") or after.get("expires_at") or "")
        _remember_lease(
            repo,
            issue,
            agent=agent,
            session=session,
            fence=fence,
            ttl_hours=ttl_hours,
            expires_at=expires_at or None,
        )
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
        _drop_lease(repo, issue)
        return {
            "released": True,
            "repo": repo,
            "issue": issue,
            "fence": fence,
            "warnings": released.get("warnings") or [],
        }


def heartbeat_job(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    fence: int,
    holder_principal: str | None = None,
    ttl_hours: float = DEFAULT_TTL_HOURS,
) -> dict[str, Any]:
    """Renew a job-board lease TTL (#2785)."""
    _validate_ttl(ttl_hours)
    _require_fence(repo, issue, fence)
    with claims_mod.hold_issue(repo, issue):
        status = claims_mod.check(repo, issue)
        if not status["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
        if not status.get("held") or (status.get("agent"), status.get("session")) != (agent, session):
            raise StaleFenceError(f"session {session!r} does not hold {repo}#{issue}")
        posted = claims_mod.post_lease(repo, issue, agent, session, "heartbeat", ttl_hours=ttl_hours)
        lease = posted["result"]
        expires_at = str(lease.get("expires_at") or status.get("expires_at") or "")
        _remember_lease(
            repo,
            issue,
            agent=agent,
            session=session,
            fence=fence,
            ttl_hours=ttl_hours,
            expires_at=expires_at or None,
        )
        return {
            "heartbeated": True,
            "repo": repo,
            "issue": issue,
            "session": session,
            "fence": fence,
            "expires_at": expires_at or None,
            "warnings": posted.get("warnings") or [],
        }


def sweep_expired_job_claims(*, now: datetime | None = None) -> dict[str, Any]:
    """Release heartbeated job-board leases past their TTL (#2785)."""
    moment = now or datetime.now(UTC)
    with _lease_guard:
        snapshot = list(_lease_state.items())
    released = 0
    errors: list[str] = []
    for _key, row in snapshot:
        repo, issue, agent, session, fence, expires, _ttl = row
        if moment < expires:
            continue
        try:
            release_job(
                repo,
                issue,
                agent=agent,
                session=session,
                fence=fence,
                reason="agent_bus lease expired",
            )
            released += 1
        except Exception as exc:  # noqa: BLE001 — sweep continues on one failure
            msg = f"{repo}#{issue}: {exc}"
            errors.append(msg)
            log.warning("jobs: expiry sweep failed for %s#%s: %s", repo, issue, exc)
    return {"released": released, "errors": errors}
