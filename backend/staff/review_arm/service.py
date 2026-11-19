"""One bounded pr-remediator cycle with injected hub capabilities.

No provider or GitHub credentials live here. Deployment wiring must supply the
existing dispatch policy (including rate/capacity admission) and the arm-only
capability; dry-run never calls either capability.

A cycle first processes finished review runs (verdict phase, see
``processor.py``), then runs intake (review dispatch / arm / escalate). The
service publishes a status snapshot with the last intake, dispatch, verdict
and arm times and raises one stall alert per episode when the queue is
non-empty but nothing was dispatched or armed for ``stall_minutes``.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter, deque
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from staff.review_arm.intake import Backoff
from staff.review_arm.policy import HELD_PRS, Candidate, Decision, decide
from staff.review_arm.processor import VerdictProcessor

log = logging.getLogger(__name__)

RECENT_DECISIONS = 50


class HubPort(Protocol):
    def candidates(self) -> Iterable[Candidate]: ...
    def refresh(self, candidate: Candidate) -> Candidate: ...
    def dispatch(self, candidate: Candidate, decision: Decision) -> None: ...
    def arm(self, candidate: Candidate) -> None: ...
    def escalate(self, candidate: Candidate, reason: str) -> None: ...


Alert = Callable[[str, str], None]


def _iso(ts: datetime | None) -> str:
    return ts.isoformat() if ts else ""


class ReviewArmService:
    """Single-flight loop; all state published as detached read-only snapshots."""

    def __init__(
        self,
        port: HubPort,
        *,
        enabled: bool = False,
        dry_run: bool = True,
        held_prs: frozenset[tuple[str, int]] = HELD_PRS,
        processor: VerdictProcessor | None = None,
        max_dispatch: int = 5,
        stall_minutes: int = 60,
        alert: Alert | None = None,
        host: str = "",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_dispatch < 0 or stall_minutes < 1:
            raise ValueError("max_dispatch must be >= 0 and stall_minutes >= 1")
        self._port = port
        self.enabled = enabled
        self.dry_run = dry_run
        self.host = host
        self._held_prs = held_prs
        self._processor = processor
        self._max_dispatch = max_dispatch
        self._stall = timedelta(minutes=stall_minutes)
        self._alert = alert
        self._clock = clock
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._started = clock()
        self._last_cycle = ""
        self._counts: dict[str, int] = {}
        self._escalations: list[dict[str, str | int]] = []
        self._times: dict[str, datetime | None] = {"intake": None, "dispatch": None, "verdict": None, "arm": None}
        self._queue_depth = 0
        self._recent: deque[dict[str, Any]] = deque(maxlen=RECENT_DECISIONS)
        self._stalled_since: datetime | None = None

    def status(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "role": "pr-remediator",
                "enabled": self.enabled,
                "dry_run": self.dry_run,
                "host": self.host,
                "last_cycle": self._last_cycle,
                "last_intake": _iso(self._times["intake"]),
                "last_dispatch": _iso(self._times["dispatch"]),
                "last_verdict": _iso(self._times["verdict"]),
                "last_arm": _iso(self._times["arm"]),
                "queue_depth": self._queue_depth,
                "stalled_since": _iso(self._stalled_since),
                "counts": dict(self._counts),
                "escalations": [dict(e) for e in self._escalations],
                "recent_decisions": [dict(d) for d in self._recent],
            }

    def _note(self, kind: str, **fields: Any) -> None:
        now = self._clock()
        with self._state_lock:
            if kind in self._times:
                self._times[kind] = now
            if fields:
                self._recent.append({"at": now.isoformat(), "event": kind, **fields})

    def cycle(self) -> None:
        """Disabled means no reads; errors fail closed and are visible by reason."""
        if not self.enabled or not self._lock.acquire(blocking=False):
            return
        counts: Counter[str] = Counter()
        escalations: list[dict[str, str | int]] = []
        try:
            self._verdict_phase(counts)
            deferred = self._intake_phase(counts, escalations)
            pending = len(self._processor.pending()) if self._processor else 0
            with self._state_lock:
                self._queue_depth = deferred + pending
        except Exception:  # noqa: BLE001 — failures must never trigger a mutation.
            counts["source_error"] += 1
            log.exception("review-arm source unavailable; cycle stopped")
        finally:
            with self._state_lock:
                self._last_cycle = self._clock().isoformat()
                self._counts = dict(counts)
                self._escalations = escalations[-100:]
            self._lock.release()
        self.check_stall()

    def _verdict_phase(self, counts: Counter[str]) -> None:
        if self._processor is None:
            return
        for dispatch in self._processor.pending():
            try:
                result = self._processor.process(dispatch)
            except Backoff:
                raise
            except Exception:  # noqa: BLE001 — one bad run never blocks the others
                counts["verdict_error"] += 1
                log.exception("review-arm verdict processing failed for %s", dispatch.get("run_id"))
                continue
            if result is None:
                continue
            counts[f"verdict_{result.decision.action}"] += 1
            fields = {
                "repo": result.repo,
                "pr": result.pr,
                "head": result.head[:8],
                "run": result.run_id,
                "verdict": result.verdict,
                "action": result.decision.action,
                "reason": result.decision.reason,
            }
            self._note("verdict", **fields)
            if result.armed or (self.dry_run and result.decision.action == "arm"):
                self._note("arm")

    def _intake_phase(self, counts: Counter[str], escalations: list[dict[str, str | int]]) -> int:
        """Returns how many review obligations were deferred (queue depth)."""
        budget = {"dispatch": self._max_dispatch, "deferred": 0}
        for candidate in self._port.candidates():
            self._process(candidate, counts, escalations, budget)
        self._note("intake")
        return budget["deferred"]

    def _process(
        self,
        c: Candidate,
        counts: Counter[str],
        escalations: list[dict[str, str | int]],
        budget: dict[str, int],
    ) -> None:
        decision: Decision | None = None
        try:
            decision = decide(c, self._held_prs)
            if decision.action == "review" and budget["dispatch"] <= 0:
                counts["dispatch_cap_defer"] += 1
                budget["deferred"] += 1
                return
            if decision.action in {"arm", "review"} and not self.dry_run:
                fresh = self._port.refresh(c)
                if (fresh.repo, fresh.pr, fresh.head_sha) != (c.repo, c.pr, c.head_sha):
                    counts["head_moved"] += 1
                    return
                c = fresh
                decision = decide(c, self._held_prs)
            key = decision.reason if decision.action == "skip" else decision.action
            counts[key] += 1
            log.info(
                "review-arm %s#%s @%s: %s (%s) dry_run=%s",
                c.repo,
                c.pr,
                c.head_sha,
                decision.action,
                decision.reason,
                self.dry_run,
            )
            if decision.action != "skip":
                self._note(
                    "decision",
                    repo=c.repo,
                    pr=c.pr,
                    head=c.head_sha[:8],
                    action=decision.action,
                    reason=decision.reason,
                    provider=decision.provider,
                )
            if decision.action == "escalate":
                escalations.append({"repo": c.repo, "pr": c.pr, "head_sha": c.head_sha, "reason": decision.reason})
            if decision.action == "review":
                budget["dispatch"] -= 1
            if self.dry_run:
                if decision.action in {"review", "arm"}:
                    self._note("dispatch" if decision.action == "review" else "arm")
                if decision.action == "review":
                    budget["deferred"] += 1  # the obligation stays open: nothing was dispatched
                return
            if decision.action == "review":
                self._port.dispatch(c, decision)
                self._note("dispatch")
            elif decision.action == "arm":
                self._port.arm(c)
                self._note("arm")
            elif decision.action == "escalate":
                self._port.escalate(c, decision.reason)
        except Backoff:
            raise
        except Exception:  # noqa: BLE001 — admission and arm refusals are terminal for this PR/cycle.
            counts["action_error"] += 1
            if decision is not None and decision.action == "review":
                budget["deferred"] += 1
            log.exception("review-arm refused %s#%s", c.repo, c.pr)

    def check_stall(self) -> bool:
        """Alert once per episode: queue non-empty, no dispatch/arm for ``stall_minutes``.

        Post: returns whether the service is stalled now; progress (a dispatch or
        arm) or an empty queue ends the episode so a later stall alerts again.
        """
        now = self._clock()
        with self._state_lock:
            progress = max(t for t in (self._started, self._times["dispatch"], self._times["arm"]) if t)
            stalled = self.enabled and self._queue_depth > 0 and now - progress >= self._stall
            first = stalled and self._stalled_since is None
            self._stalled_since = (self._stalled_since or now) if stalled else None
            depth = self._queue_depth
        if first and self._alert is not None:
            minutes = int((now - progress).total_seconds() // 60)
            try:
                self._alert(
                    "review-arm stalled",
                    f"queue depth {depth}; no review dispatched and no PR armed for {minutes} min"
                    f" (host {self.host or 'unknown'}, dry_run={self.dry_run})",
                )
            except Exception:  # noqa: BLE001 — an alert failure must not stop the loop
                log.exception("review-arm stall alert failed")
        return stalled
