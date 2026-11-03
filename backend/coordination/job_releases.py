"""Read model for job-board claim releases (epic #2781 / slice toward #2785).

Authoritative release transitions are already emitted as ``job_board`` activity
envelopes from :func:`activity_producers.record_job_claim_activity`. This module
projects them for ``GET /api/coordination/jobs/releases`` without a second log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import activity as activity_mod

_RELEASE_KINDS = frozenset({"release", "expiry", "conflict"})
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500
_MAX_SCAN = 5000


def _parse_since(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row_from_envelope(env: Any) -> dict[str, Any]:
    payload = env.payload or {}
    agent = str(payload.get("agent") or "")
    if not agent and env.principal.startswith("agent-"):
        agent = env.principal.removeprefix("agent-")
    reason = str(payload.get("reason") or env.kind)
    return {
        "repo": env.repo or "",
        "issue": env.issue,
        "agent": agent,
        "session": env.session_id,
        "reason": reason,
        "released_at": env.ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "kind": env.kind,
    }


def list_job_releases(*, since: str | None = None, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    """Return recent job-board release rows newest-first, optionally filtered by ``since`` (ISO-8601)."""
    bounded = max(1, min(limit, _MAX_LIMIT))
    since_dt = _parse_since(since)
    store = activity_mod.get_activity_store()
    envelopes = store.scan_recent(limit=_MAX_SCAN)
    rows: list[dict[str, Any]] = []
    for env in envelopes:
        if env.source != "job_board" or env.kind not in _RELEASE_KINDS:
            continue
        if since_dt is not None and env.ts.astimezone(UTC) < since_dt:
            continue
        if env.issue is None or not env.repo:
            continue
        rows.append(_row_from_envelope(env))
        if len(rows) >= bounded:
            break
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {"releases": rows, "complete": len(rows) < bounded, "generated_at": generated_at}
