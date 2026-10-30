"""In-process producer adapter for the fleet Activity Log (issue #2859, epic #2856 slice A3).

Producers (staff runs, coordination presence/messages, agent-bus hooks) call
:func:`emit_activity` and never touch :class:`activity.ActivityStore` directly (DRY,
LoD). Every failure is caught here: an activity-log outage must never block or
corrupt the producer's own operation.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from activity import ActivityEnvelope, ActivitySource, get_activity_store

if TYPE_CHECKING:
    from staff.store import RunRecord

log = logging.getLogger("dashboard.activity_producers")

# Opaque hub-local identity for in-process producers; never a secret or network address.
PRODUCER_PRINCIPAL = "hub:activity-producer"
JobClaimKind = Literal["claim", "release", "expiry", "conflict"]
_SECRET_TOKEN_PREFIXES = ("abs_", "ghs_", "ghp_", "github_pat_", "Bearer ")
_COMMENT_ID_RE = re.compile(r"issuecomment-(\d+)")

_seq_lock = threading.Lock()
_next_seq: dict[tuple[str, str], int] = {}


def _reserve_seq(source: str, session_id: str) -> int:
    """Next monotonically increasing seq for ``(source, session_id)`` in this process."""
    key = (source, session_id)
    with _seq_lock:
        seq = _next_seq.get(key, 0) + 1
        _next_seq[key] = seq
        return seq


def emit_activity(
    *,
    source: ActivitySource,
    session_id: str,
    node: str,
    kind: str,
    seq: int | None = None,
    repo: str | None = None,
    issue: int | None = None,
    branch: str | None = None,
    pr: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one envelope to the activity log; never raises.

    Pre: ``source``/``session_id``/``node``/``kind`` describe one real event.
    Post: either a durable (possibly deduplicated) envelope was appended, or the
    failure was logged and swallowed — the caller's own operation is unaffected.

    ``seq`` lets a caller reuse a sequence number it already owns (e.g. the staff
    run event log's own per-run counter) so a retried call stays idempotent;
    otherwise this process's own per-``(source, session_id)`` counter is used.
    """
    try:
        resolved_seq = seq if seq is not None else _reserve_seq(source, session_id)
        envelope = ActivityEnvelope(
            schema_version=1,
            event_id=f"{source}:{session_id}:{resolved_seq}",
            source=source,
            principal=PRODUCER_PRINCIPAL,
            node=(node or "unknown")[:128],
            session_id=session_id,
            seq=resolved_seq,
            ts=datetime.now(UTC),
            repo=repo,
            issue=issue,
            branch=branch,
            pr=pr,
            kind=kind,
            redaction_version=1,
            payload=payload or {},
        )
        get_activity_store().ingest([envelope], principal=PRODUCER_PRINCIPAL)
    except Exception:  # noqa: BLE001 — an activity-log outage must never break the producer
        log.warning(
            "activity adapter failed for source=%s session_id=%s kind=%s", source, session_id, kind, exc_info=True
        )


def record_staff_run_activity(rec: RunRecord | None, seq: int, kind: str, text: str) -> None:
    """Feed the activity log from a staff run's own event stream (#2859); no-op if the run is gone.

    Reuses the event's own seq so the adapter stays idempotent on a retry.
    """
    if rec is None:
        return
    emit_activity(
        source="staff_run",
        session_id=rec.id,
        node=rec.machine,
        kind=kind,
        seq=seq,
        repo=rec.repo or None,
        branch=rec.branch or None,
        payload={"text": text[:2000]} if text.strip() else {},
    )


def fencing_token_id_for_claim(
    *,
    repo: str,
    issue: int,
    agent: str,
    session: str,
    receipt: str = "",
    fencing_token: str | None = None,
) -> str:
    """Derive an opaque fencing token id for a claim transition; never returns a raw secret."""
    if fencing_token:
        raw = fencing_token.strip()
        if any(raw.startswith(prefix) for prefix in _SECRET_TOKEN_PREFIXES):
            return f"ftk_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
        return raw[:128]
    match = _COMMENT_ID_RE.search(receipt or "")
    if match:
        return f"lease-{match.group(1)}"
    seed = f"{repo.casefold()}#{issue}:{agent}:{session}:{receipt}"
    return f"ftk_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def record_job_claim_activity(
    *,
    kind: JobClaimKind,
    repo: str,
    issue: int,
    agent: str,
    session: str,
    holder_principal: str | None = None,
    receipt: str = "",
    fencing_token: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Emit a ``job_board`` envelope for an authoritative claim transition (#2861)."""
    if kind not in ("claim", "release", "expiry", "conflict"):
        raise ValueError(f"unsupported job claim activity kind: {kind!r}")
    if not repo or issue <= 0 or not session:
        raise ValueError("job claim activity requires valid repo, positive issue and session")
    resolved_principal = (holder_principal or (f"agent-{agent}" if agent else "unknown")).strip()
    token_id = fencing_token_id_for_claim(
        repo=repo,
        issue=issue,
        agent=agent,
        session=session,
        receipt=receipt,
        fencing_token=fencing_token,
    )
    payload: dict[str, Any] = {
        "repo": repo,
        "issue": issue,
        "holder_principal": resolved_principal,
        "session": session,
        "fencing_token_id": token_id,
    }
    if agent:
        payload["agent"] = agent
    if extra:
        payload.update(extra)
    emit_activity(
        source="job_board",
        session_id=session,
        node="hub",
        kind=kind,
        repo=repo,
        issue=issue,
        payload=payload,
    )
    return token_id
