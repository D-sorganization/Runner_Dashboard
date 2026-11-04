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
_lease_state: dict[str, tuple[str, int, str, str, int, datetime, float]] = {}
DEFAULT_TTL_HOURS = 2.0
MIN_TTL_HOURS = 0.1
MAX_TTL_HOURS = 8.0


def _lease_key(repo: str, issue: int) -> str:
    return f"{repo.casefold()}#{issue}"


def clear_lease_state() -> None:
    with _lease_guard:
        _lease_state.clear()


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
    with _lease_guard:
        _lease_state[_lease_key(repo, issue)] = (repo, issue, agent, session, fence, expires, ttl_hours)


def drop_lease(repo: str, issue: int) -> None:
    with _lease_guard:
        _lease_state.pop(_lease_key(repo, issue), None)


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
    from coordination import jobs as jobs_mod

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
            jobs_mod.release_job(
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
