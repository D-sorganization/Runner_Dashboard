"""Plain-code fallback for curator outage (#3941, barb-memory slice 5a).

Depends on slice 1 (``context_contract.py``, #3937) and slice 4
(``context_brief.py``, #3940). "The curator" is whatever scheduled process
(plain-code job or light model) normally keeps the Barb shared context brief
refreshed and its :class:`~memory.context_brief.BriefRunTracker` advancing.
This module detects that process going stale or unavailable and, only then,
produces a usable brief directly from :func:`~memory.context_brief.build_context_brief`
-- i.e. the same deterministic, no-model-call, already-guarded code path the
curator itself would use, not a parallel implementation.

Per the issue amendments: no always-on Opus for this refresh; the normal
refresh path is scheduled plain-code or a light model, with Opus reserved
for occasional deeper cleanup on request. This module never calls a model.

Staleness detection follows the health/heartbeat pattern in
``staff/persona_fallback_lease.is_scheduler_blocked_by_fallback``: fail
closed (treat the curator as down) on a missing heartbeat, a recorded
failed run, or a timestamp this check cannot safely compare -- never on a
false "it's fine" when the signal is ambiguous.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from memory.context_brief import (
    DEFAULT_MAX_BRIEF_TOKENS,
    BriefRunTracker,
    ContextBrief,
    build_context_brief,
)
from memory.models import MemoryEntry

DEFAULT_MAX_STALENESS_SECONDS = 15 * 60


@dataclass(frozen=True)
class CuratorHeartbeat:
    """The curator's last known-good refresh signal.

    ``last_refreshed_at`` is the timestamp of the curator's last successful
    rebuild of the shared context brief (e.g. a ``BriefRunTracker.record_pass``
    call). ``None`` means no successful run has ever been recorded.
    """

    last_refreshed_at: str | None
    last_run_ok: bool = True


def curator_is_stale(
    heartbeat: CuratorHeartbeat,
    *,
    now_iso: str,
    max_staleness_seconds: int = DEFAULT_MAX_STALENESS_SECONDS,
) -> bool:
    """True when the curator's normal refresh process must be treated as down.

    Fails closed to "stale" (curator down) on: no heartbeat ever recorded,
    the curator's last recorded run failing, a heartbeat or ``now_iso``
    timestamp this check cannot safely compare (missing timezone info or
    unparsable), or ``last_refreshed_at`` older than *max_staleness_seconds*.
    A false "fresh" is the unsafe direction here -- it would skip the
    fallback while the curator is genuinely down -- so every ambiguous case
    resolves to stale instead.
    """
    if heartbeat.last_refreshed_at is None or not heartbeat.last_run_ok:
        return True
    try:
        last = datetime.fromisoformat(heartbeat.last_refreshed_at)
        now = datetime.fromisoformat(now_iso)
    except (TypeError, ValueError):
        return True
    if last.utcoffset() is None or now.utcoffset() is None:
        return True
    return (now - last).total_seconds() > max_staleness_seconds


@dataclass(frozen=True)
class FallbackBriefResult:
    """The outcome of a fallback brief attempt.

    ``brief`` is ``None`` whenever no fallback brief was served -- either
    because the curator was not stale (``reason == "curator_fresh"``) or
    because the fallback engaged but found nothing eligible to serve
    (``reason == "no_eligible_entries"``). This module never fabricates a
    brief in that second case; a caller must treat ``brief is None`` as
    "nothing to show", not as a trigger to invent content.
    """

    brief: ContextBrief | None
    used_fallback: bool
    reason: str


def build_fallback_brief(
    entries: Sequence[MemoryEntry],
    *,
    now_iso: str,
    heartbeat: CuratorHeartbeat,
    tracker: BriefRunTracker | None = None,
    max_staleness_seconds: int = DEFAULT_MAX_STALENESS_SECONDS,
    max_tokens: int = DEFAULT_MAX_BRIEF_TOKENS,
    include_stale: bool = True,
) -> FallbackBriefResult:
    """Serve a plain-code context brief only when the curator is down.

    When the curator is not stale, this is a no-op: the normal curator-built
    brief is left in place and this function returns ``used_fallback=False``.
    When the curator is stale, this calls :func:`build_context_brief`
    directly -- the same deterministic, guard-composed, redaction-re-run
    code path the curator itself uses -- rather than depending on the
    curator process having run recently. If that call finds no entries to
    serve (e.g. all candidates were privacy-filtered or expired), no brief
    is fabricated; ``brief`` is ``None`` and ``reason`` explains why.
    """
    if not curator_is_stale(heartbeat, now_iso=now_iso, max_staleness_seconds=max_staleness_seconds):
        return FallbackBriefResult(brief=None, used_fallback=False, reason="curator_fresh")

    brief = build_context_brief(
        entries,
        now_iso=now_iso,
        tracker=tracker,
        max_tokens=max_tokens,
        include_stale=include_stale,
    )
    if not brief.entry_ids:
        return FallbackBriefResult(brief=None, used_fallback=True, reason="no_eligible_entries")
    return FallbackBriefResult(brief=brief, used_fallback=True, reason="curator_stale_fallback_served")
