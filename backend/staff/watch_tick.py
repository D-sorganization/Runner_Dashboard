"""Non-LLM watch tick for the merge queue and board (issue #3053, epic #3048).

Cheaper than the LLM-role cron schedules in ``scheduler.py``: probes the
merge queue/board with conditional (``If-None-Match``) GitHub requests,
diffs against the previous tick, and escalates through the *existing*
scheduler dispatch path (holds, budget, one-active-run-per-role,
Idempotency-Key) only on a threshold trip. :func:`evaluate` is pure and
fails closed on malformed input (logged and skipped, never raised).
:class:`WatchTickRunner` reuses the scheduler's own ``holds``/``budget``/
``active_runs``/``submit`` collaborators rather than dispatching in
parallel; HOLD #2657 is enforced via the same ``holds.blocking`` call.

Feature flag (default OFF): ``STAFF_WATCH_TICK_ENABLED=1``. The Actions-burn
probe (#2878) and the scheduler-loop + concrete ``GitHubProbePort`` wiring
are a follow-up once the REST shapes are confirmed; this PR ships the pure
seam (:func:`evaluate`, :class:`WatchTickRunner`, ``ETagCache``) with tests.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from staff.plan import RunRequest

logger = logging.getLogger("dashboard.staff.watch_tick")

Role = Literal["queue-shepherd", "pr-remediator", "night-watch"]
_ROLES: frozenset[str] = frozenset({"queue-shepherd", "pr-remediator", "night-watch"})


def watch_tick_enabled() -> bool:
    """``STAFF_WATCH_TICK_ENABLED=1`` opts in; default OFF."""
    return os.environ.get("STAFF_WATCH_TICK_ENABLED", "").strip() == "1"


def default_ledger_path() -> Path:
    override = os.environ.get("STAFF_WATCH_TICK_LEDGER", "").strip()
    return Path(override) if override else Path.home() / ".config/runner-dashboard/staff_watch_tick_ledger.jsonl"


# --- Snapshot model ---
@dataclass(frozen=True)
class RepoSnapshot:
    """One repo's watch-tick state: queue counters, board unanswered-to-Barb
    threads, and ``failure_timestamps`` (epoch seconds) for the storm ledger."""

    repo: str
    queued_count: int = 0
    oldest_age_seconds: int = 0
    failed_heads: tuple[str, ...] = ()
    unanswered_to_barb: tuple[str, ...] = ()
    failure_timestamps: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not self.repo or not self.repo.strip():
            raise ValueError("RepoSnapshot.repo must be non-empty")


@dataclass(frozen=True)
class WatchThresholds:
    """Escalation thresholds; defaults are deliberately conservative."""

    queue_age_seconds: int = 30 * 60
    storm_count: int = 3
    storm_window_seconds: int = 15 * 60

    def __post_init__(self) -> None:
        if self.queue_age_seconds <= 0 or self.storm_count <= 0 or self.storm_window_seconds <= 0:
            raise ValueError("WatchThresholds fields must be positive")


@dataclass(frozen=True)
class Escalation:
    """One threshold trip, ready to dispatch through the normal scheduler path."""

    role: Role
    repo: str
    trigger: str
    reason: str
    hour_bucket: str

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(f"unknown watch-tick role: {self.role!r}")

    def idempotency_key(self) -> str:
        """Per (role, repo, trigger, hour) — at most one dispatch per hour."""
        return f"watch-tick:{self.role}:{self.repo}:{self.trigger}:{self.hour_bucket}"


# --- Pure evaluation ---
def evaluate(
    previous: dict[str, RepoSnapshot] | None,
    current: dict[str, RepoSnapshot],
    thresholds: WatchThresholds,
    *,
    now: datetime | None = None,
) -> list[Escalation]:
    """Diff ``previous`` against ``current`` and return escalations to dispatch.

    DbC: ``current`` must be a ``dict[str, RepoSnapshot]``; any other shape,
    or a non-``RepoSnapshot`` value for a repo, fails closed (skipped and
    logged) rather than raising into the tick loop.
    """
    if not isinstance(current, dict):
        logger.warning("watch_tick.evaluate: current must be a dict, got %r; failing closed", type(current))
        return []
    now = now or datetime.now(UTC)
    hour_bucket = now.strftime("%Y-%m-%dT%H")
    previous = previous if isinstance(previous, dict) else {}
    out: list[Escalation] = []

    for repo, snap in current.items():
        if not isinstance(snap, RepoSnapshot) or snap.repo != repo:
            logger.warning("watch_tick.evaluate: malformed snapshot for %r; skipping", repo)
            continue
        prev = previous.get(repo) if isinstance(previous.get(repo), RepoSnapshot) else None
        prev_failed = prev.failed_heads if prev else ()
        prev_unanswered = prev.unanswered_to_barb if prev else ()

        repeated_failed = set(snap.failed_heads) & set(prev_failed)
        if repeated_failed:
            reason = f"heads failed twice in a row: {sorted(repeated_failed)}"
            out.append(Escalation("pr-remediator", repo, "merge_queue_failed_head", reason, hour_bucket))
        elif snap.queued_count > 0 and snap.oldest_age_seconds > thresholds.queue_age_seconds:
            reason = f"oldest queued item is {snap.oldest_age_seconds}s old (> {thresholds.queue_age_seconds}s)"
            out.append(Escalation("queue-shepherd", repo, "merge_queue_age", reason, hour_bucket))

        new_unanswered = set(snap.unanswered_to_barb) - set(prev_unanswered)
        if new_unanswered:
            reason = f"new unanswered messages to Barb: {sorted(new_unanswered)}"
            out.append(Escalation("night-watch", repo, "board_new_unanswered", reason, hour_bucket))

        window = thresholds.storm_window_seconds
        recent_failures = [t for t in snap.failure_timestamps if 0 <= now.timestamp() - t <= window]
        if len(recent_failures) >= thresholds.storm_count:
            reason = f"{len(recent_failures)} failures of the same workflow within {window}s"
            out.append(Escalation("night-watch", repo, "failing_run_storm", reason, hour_bucket))

    return out


# --- Conditional-request probing ---
@dataclass
class ProbeResponse:
    """Result of one conditional GitHub REST GET."""

    status_code: int
    etag: str | None = None
    body: dict[str, Any] | None = None  # None on 304 Not Modified
    rate_limit_reset: float | None = None  # epoch seconds; set on 403/429


class GitHubProbePort(Protocol):
    """One conditional GET per call, nothing else (LoD)."""

    def get(self, path: str, *, etag: str | None) -> ProbeResponse: ...


@dataclass
class ETagCache:
    """Remembers the last ETag seen per cache key so probes stay conditional."""

    _etags: dict[str, str] = field(default_factory=dict)

    def etag_for(self, key: str) -> str | None:
        return self._etags.get(key)

    def remember(self, key: str, etag: str | None) -> None:
        if etag:
            self._etags[key] = etag


@dataclass
class RateLimitState:
    """Tick-level backoff: a 403/429 disables all probing until GitHub's reset time."""

    disabled_until: float | None = None

    def is_disabled(self, now: float) -> bool:
        return self.disabled_until is not None and now < self.disabled_until

    def trip(self, reset_at: float) -> None:
        """Disable the tick until ``reset_at`` and log exactly one warning."""
        self.disabled_until = reset_at
        logger.warning(
            "watch_tick: GitHub rate limit hit; disabling the watch tick until %s",
            datetime.fromtimestamp(reset_at, tz=UTC).isoformat(),
        )


# --- Ledger ---
def append_ledger(
    path: Path, snapshot: RepoSnapshot, escalations: list[Escalation], *, now: datetime | None = None
) -> None:
    """Append one delta-ledger row (also the storm-ledger record) as JSONL."""
    now = now or datetime.now(UTC)
    record = {"ts": now.isoformat(), **asdict(snapshot), "escalations": [asdict(e) for e in escalations]}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


# --- Tick runner ---
@dataclass
class WatchTickRunner:
    """Runs one tick: probe -> evaluate -> gate -> dispatch.

    Collaborators (LoD) are a ``StaffScheduler``'s own ``holds``/``budget``/
    ``runner`` attributes (tests pass fakes with the same narrow surface)
    plus the shared ``IdempotencyStore`` — the gate and run submission are
    the ones ``StaffScheduler.tick`` already uses.
    """

    holds: Any  # staff.holds.HoldsList
    budget: Any  # staff.budget.BudgetGuard
    roles: Any  # Mapping[str, RoleSpec] (StaffRunner.roles())
    active_runs: Any  # Callable[[], list[RunRecord]] (RunStore.active_runs)
    submit: Any  # Callable[[RunRequest], Any] (StaffRunner.submit)
    idempotency: Any  # staff.idempotency.IdempotencyStore
    thresholds: WatchThresholds = field(default_factory=WatchThresholds)
    ledger_path: Path = field(default_factory=default_ledger_path)
    rate_limit: RateLimitState = field(default_factory=RateLimitState)
    _previous: dict[str, RepoSnapshot] = field(default_factory=dict)

    IDEMPOTENCY_ENDPOINT = "staff.watch_tick"
    IDEMPOTENCY_PRINCIPAL = "watch-tick"

    def run(self, snapshots: dict[str, RepoSnapshot], *, now: datetime | None = None) -> list[Escalation]:
        """One tick: diff against the last tick, dispatch what clears every gate."""
        if self.rate_limit.is_disabled(time.time()):
            logger.info("watch_tick: skipping tick, rate-limit backoff active")
            return []
        if not isinstance(snapshots, dict):
            logger.warning("watch_tick: run() needs a dict of snapshots; failing closed")
            return []
        now = now or datetime.now(UTC)
        escalations = evaluate(self._previous, snapshots, self.thresholds, now=now)
        for repo, snap in snapshots.items():
            if isinstance(snap, RepoSnapshot):
                append_ledger(self.ledger_path, snap, [e for e in escalations if e.repo == repo], now=now)
        dispatched = [esc for esc in escalations if self._dispatch_one(esc)]
        self._previous = dict(snapshots)
        return dispatched

    def record_rate_limit(self, reset_at: float) -> None:
        """Called by the probe layer on a 403/429 so the tick backs off."""
        self.rate_limit.trip(reset_at)

    def _dispatch_one(self, esc: Escalation) -> bool:
        hold = self.holds.blocking(esc.role, esc.repo)
        if hold is not None:
            logger.info("watch_tick: %s on %s blocked by hold: %s", esc.role, esc.repo, hold)
            return False
        role_spec = self.roles.get(esc.role)
        if role_spec is None:
            logger.warning("watch_tick: unknown role %r; failing closed", esc.role)
            return False
        if any(getattr(r, "role", None) == esc.role for r in self.active_runs()):
            logger.info("watch_tick: %s already has an active run; skipping", esc.role)
            return False
        budget_ok, budget_reason = self.budget.can_run(role_spec)
        if not budget_ok:
            logger.info("watch_tick: %s blocked by budget: %s", esc.role, budget_reason)
            return False
        key = esc.idempotency_key()
        reservation = self.idempotency.reserve(key, self.IDEMPOTENCY_ENDPOINT, self.IDEMPOTENCY_PRINCIPAL, key)
        if reservation.state != "acquired":
            logger.info("watch_tick: %s already dispatched this hour (%s)", key, reservation.state)
            return False
        try:
            req = RunRequest(role=esc.role, repo=esc.repo, prompt=esc.reason, requested_by=self.IDEMPOTENCY_PRINCIPAL)
            self.submit(req)
        except Exception:
            self.idempotency.release(
                key, self.IDEMPOTENCY_ENDPOINT, self.IDEMPOTENCY_PRINCIPAL, operation_id=reservation.operation_id
            )
            raise
        self.idempotency.complete(
            key,
            self.IDEMPOTENCY_ENDPOINT,
            self.IDEMPOTENCY_PRINCIPAL,
            200,
            {},
            "dispatched",
            operation_id=reservation.operation_id,
        )
        return True
