"""In-process TTL bookkeeping for job-board claims (#2785)."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from coordination import claims as claims_mod
from coordination.board import RMScriptError

log = logging.getLogger("dashboard.coordination.job_board_lease")

_lease_guard = threading.Lock()
_LeaseRow = tuple[str, int, str, str, int, datetime, float]
_lease_state: dict[str, _LeaseRow] = {}
_release_failures: dict[str, int] = {}  # consecutive failed sweeper releases per lease key
DEFAULT_TTL_HOURS = 2.0
MIN_TTL_HOURS = 0.1
MAX_TTL_HOURS = 8.0
# An expired row whose release keeps failing is dead-lettered after this many attempts (#4270 B-11).
MAX_JOB_RELEASE_ATTEMPTS = 5


def _lease_key(repo: str, issue: int) -> str:
    return f"{repo.casefold()}#{issue}"


def clear_lease_state() -> None:
    with _lease_guard:
        _lease_state.clear()
        _release_failures.clear()


def parse_expires(raw: str | None, *, ttl_hours: float) -> datetime:
    if raw:
        text = raw.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC) + timedelta(hours=ttl_hours)


def remember_lease(
    repo: str,
    issue: int,
    *,
    agent: str,
    session: str,
    fence: int,
    ttl_hours: float,
    expires_at: str | None,
) -> None:
    expires = parse_expires(expires_at, ttl_hours=ttl_hours)
    key = _lease_key(repo, issue)
    with _lease_guard:
        _lease_state[key] = (repo, issue, agent, session, fence, expires, ttl_hours)
        _release_failures.pop(key, None)  # a claim or renewal starts with a full retry budget


def drop_lease(repo: str, issue: int) -> None:
    key = _lease_key(repo, issue)
    with _lease_guard:
        _lease_state.pop(key, None)
        _release_failures.pop(key, None)


def validate_ttl(ttl_hours: float) -> None:
    if not MIN_TTL_HOURS <= ttl_hours <= MAX_TTL_HOURS:
        raise ValueError(f"ttl_hours must be between {MIN_TTL_HOURS} and {MAX_TTL_HOURS}")


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
    from coordination import jobs as jobs_mod

    validate_ttl(ttl_hours)
    jobs_mod._require_fence(repo, issue, fence)
    with claims_mod.hold_issue(repo, issue):
        status = claims_mod.check(repo, issue)
        if not status["available"]:
            raise RMScriptError(f"check_agent_claim unavailable: {status['reason']}")
        if not status.get("held") or (status.get("agent"), status.get("session")) != (agent, session):
            raise jobs_mod.StaleFenceError(f"session {session!r} does not hold {repo}#{issue}")
        posted = claims_mod.post_lease(repo, issue, agent, session, "heartbeat", ttl_hours=ttl_hours)
        lease = posted["result"]
        expires_at = str(lease.get("expires_at") or status.get("expires_at") or "")
        remember_lease(
            repo,
            issue,
            agent=agent,
            session=session,
            fence=fence,
            ttl_hours=ttl_hours,
            expires_at=expires_at or None,
        )
        # fmt: off
        return {
            "heartbeated": True, "repo": repo, "issue": issue, "session": session, "fence": fence,
            "expires_at": expires_at or None, "warnings": posted.get("warnings") or [],
        }
        # fmt: on


def _forget_swept_row(key: str, row: _LeaseRow) -> None:
    """Drop ``row`` unless a newer claim or renewal replaced it while the sweep ran."""
    with _lease_guard:
        if _lease_state.get(key) is row:
            del _lease_state[key]
            _release_failures.pop(key, None)


def _count_release_failure(key: str, row: _LeaseRow) -> int:
    """Record one failed release of ``row``; 0 when the row was replaced and the failure is moot."""
    with _lease_guard:
        if _lease_state.get(key) is not row:
            return 0
        failures = _release_failures.get(key, 0) + 1
        _release_failures[key] = failures
        return failures


def sweep_expired_job_claims(*, now: datetime | None = None) -> dict[str, Any]:
    """Release heartbeated job-board leases past their TTL (#2785).

    Every expired row settles (#4270 B-11): released as its holder agent; ``forgotten``
    without a release when the job is no longer that holder's (stale fence); or, when
    the release fails, kept for a retry and dead-lettered (ERROR log, then forgotten)
    at ``MAX_JOB_RELEASE_ATTEMPTS``. One row's failure never stops the others.
    """
    from coordination import jobs as jobs_mod

    moment = now or datetime.now(UTC)
    with _lease_guard:
        snapshot = list(_lease_state.items())
    released = forgotten = dead_lettered = 0
    errors: list[str] = []
    for key, row in snapshot:
        repo, issue, agent, session, fence, expires, _ttl = row
        if moment < expires:
            continue
        try:
            jobs_mod.release_job(
                repo,
                issue,
                agent=agent,
                session=session,
                fence=fence,
                reason="agent_bus lease expired",
            )
            released += 1
        except jobs_mod.StaleFenceError as exc:
            _forget_swept_row(key, row)
            forgotten += 1
            log.info("jobs: expiry sweep forgot %s#%s, no longer held by %s: %s", repo, issue, session, exc)
        except Exception as exc:  # noqa: BLE001 — sweep continues on one failure
            failures = _count_release_failure(key, row)
            if not failures:
                log.info("jobs: expiry sweep failure for %s#%s is moot, the row was replaced: %s", repo, issue, exc)
                continue
            errors.append(f"{repo}#{issue}: {exc}")
            if failures < MAX_JOB_RELEASE_ATTEMPTS:
                log.warning("jobs: expiry sweep failed for %s#%s: %s", repo, issue, exc)
                continue
            _forget_swept_row(key, row)
            dead_lettered += 1
            log.error(
                "job-board sweeper dead-letter: giving up on %s#%s for session %s (agent %s) after %d failed "
                "releases; the lease stays until its own expiry: %s",
                repo, issue, session, agent, failures, exc,
            )  # fmt: skip
    return {"released": released, "errors": errors, "forgotten": forgotten, "dead_lettered": dead_lettered}
