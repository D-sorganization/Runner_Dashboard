"""One bounded pr-remediator cycle with injected hub capabilities.

No provider or GitHub credentials live here. Deployment wiring must supply the
existing dispatch policy (including rate/capacity admission) and shepherd arm
capability; dry-run never calls either capability.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from staff.review_arm.intake import Backoff
from staff.review_arm.policy import HELD_PRS, Candidate, Decision, decide

log = logging.getLogger(__name__)


class HubPort(Protocol):
    def candidates(self) -> Iterable[Candidate]: ...
    def refresh(self, candidate: Candidate) -> Candidate: ...
    def dispatch(self, candidate: Candidate, decision: Decision) -> None: ...
    def arm(self, candidate: Candidate) -> None: ...
    def escalate(self, candidate: Candidate, reason: str) -> None: ...


class ReviewArmService:
    """Single-flight loop; all state published as detached read-only snapshots."""

    def __init__(
        self,
        port: HubPort,
        *,
        enabled: bool = False,
        dry_run: bool = True,
        held_prs: frozenset[tuple[str, int]] = HELD_PRS,
    ) -> None:
        self._port = port
        self.enabled = enabled
        self.dry_run = dry_run
        self._held_prs = held_prs
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_cycle = ""
        self._counts: dict[str, int] = {}
        self._escalations: list[dict[str, str | int]] = []

    def status(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "role": "pr-remediator",
                "enabled": self.enabled,
                "dry_run": self.dry_run,
                "last_cycle": self._last_cycle,
                "counts": dict(self._counts),
                "escalations": [dict(e) for e in self._escalations],
            }

    def cycle(self) -> None:
        """Disabled means no reads; errors fail closed and are visible by reason."""
        if not self.enabled or not self._lock.acquire(blocking=False):
            return
        counts: Counter[str] = Counter()
        escalations: list[dict[str, str | int]] = []
        try:
            for candidate in self._port.candidates():
                self._process(candidate, counts, escalations)
        except Exception:  # noqa: BLE001 — failures must never trigger a mutation.
            counts["source_error"] += 1
            log.exception("review-arm source unavailable; cycle stopped")
        finally:
            with self._state_lock:
                self._last_cycle = datetime.now(UTC).isoformat()
                self._counts = dict(counts)
                self._escalations = escalations[-100:]
            self._lock.release()

    def _process(self, c: Candidate, counts: Counter[str], escalations: list[dict[str, str | int]]) -> None:
        try:
            decision = decide(c, self._held_prs)
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
            if decision.action == "escalate":
                escalations.append({"repo": c.repo, "pr": c.pr, "head_sha": c.head_sha, "reason": decision.reason})
            if self.dry_run:
                return
            if decision.action == "review":
                self._port.dispatch(c, decision)
            elif decision.action == "arm":
                self._port.arm(c)
            elif decision.action == "escalate":
                self._port.escalate(c, decision.reason)
        except Backoff:
            raise
        except Exception:  # noqa: BLE001 — admission and arm refusals are terminal for this PR/cycle.
            counts["action_error"] += 1
            log.exception("review-arm refused %s#%s", c.repo, c.pr)
