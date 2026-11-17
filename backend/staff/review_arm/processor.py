"""Act on finished review runs: record, block, escalate, relay findings, arm (armd.py parity).

For each dispatch the ledger has not handled yet, the processor reads the run's
outcome (recovering it from events after a hub restart or a failed post-run
step), records the verdict, and then:

- changes with BLOCKING findings / escalate: the head is blocked (sticky until
  a new head or an operator override); at round >= 2 it escalates to the owner;
- an effective approve (policy 2: changes with zero BLOCKING counts) goes
  through :func:`policy.arm_gate` and, when it says ``arm``, the arm-only
  capability, pinned to the reviewed head;
- the reviewer's final message is posted to the PR (relay.py), never on
  ``work_private``.

Dry-run records and logs every decision but calls no write port (no arm, no
comment, no owner notification).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from staff.review_arm.armer import ArmOutcome
from staff.review_arm.ledger import Ledger, Verdict
from staff.review_arm.policy import Decision, LiveHead, arm_gate
from staff.review_arm.verdicts import RunOutcome

log = logging.getLogger(__name__)

NO_RELAY_REPOS = frozenset({"work_private"})
SUMMARY_LIMIT = 4000


class ProcessorPort(Protocol):
    def run_outcome(self, run_id: str) -> RunOutcome: ...
    def live(self, repo: str, pr: int) -> LiveHead: ...
    def arm(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> ArmOutcome: ...
    def post_findings(self, repo: str, pr: int, head: str, run_id: str, text: str) -> None: ...
    def escalate(self, repo: str, pr: int, head: str, reason: str, detail: str) -> None: ...


@dataclass(frozen=True)
class Processed:
    run_id: str
    repo: str
    pr: int
    head: str
    verdict: str
    decision: Decision
    armed: bool = False


class VerdictProcessor:
    def __init__(
        self,
        ledger: Ledger,
        port: ProcessorPort,
        *,
        dry_run: bool = True,
        held_prs: Callable[[], frozenset[tuple[str, int]]] = frozenset,
    ) -> None:
        self.ledger = ledger
        self._port = port
        self.dry_run = dry_run
        self._held_prs = held_prs

    def holds(self) -> frozenset[tuple[str, int]]:
        return self._held_prs() | self.ledger.holds()

    def pending(self) -> list[dict[str, Any]]:
        return self.ledger.pending_dispatches()

    def process(self, dispatch: dict[str, Any]) -> Processed | None:
        """Handle one dispatch; None while its run is still active.

        Post: the run is marked handled exactly once (a crash before that point
        re-processes it next cycle; arm and escalation are idempotent).
        """
        run_id, repo, pr, head = str(dispatch["run_id"]), str(dispatch["repo"]), int(dispatch["pr"]), dispatch["head"]
        round_no = int(dispatch.get("round") or 1)
        out = self._port.run_outcome(run_id)
        if out.kind == "pending":
            return None
        if out.kind != "verdict":
            self.ledger.mark_handled(run_id, outcome=out.kind)
            return Processed(run_id, repo, pr, head, "", Decision("requeue", out.kind))
        if out.verdict_head and not head.startswith(out.verdict_head):
            self.ledger.mark_handled(run_id, outcome="verdict_head_mismatch")
            return Processed(run_id, repo, pr, head, out.verdict, Decision("skip", "verdict_head_mismatch"))
        result = self._apply(run_id, repo, pr, head, round_no, str(dispatch.get("provider") or "claude"), out)
        if not self.dry_run and repo not in NO_RELAY_REPOS and out.text.strip():
            try:
                self._port.post_findings(repo, pr, head, run_id, out.text)
            except Exception:  # noqa: BLE001 — a failed relay never undoes the verdict
                log.exception("review-arm: findings relay failed for %s#%s (%s)", repo, pr, run_id)
        self.ledger.mark_handled(run_id, outcome=f"{out.verdict}:{result.decision.action}:{result.decision.reason}")
        log.info(
            "review-arm verdict %s %s#%s @%s: %s blocking=%s -> %s (%s) dry_run=%s",
            run_id,
            repo,
            pr,
            head[:8],
            out.verdict,
            out.blocking,
            result.decision.action,
            result.decision.reason,
            self.dry_run,
        )
        return result

    def _apply(
        self, run_id: str, repo: str, pr: int, head: str, round_no: int, provider: str, out: RunOutcome
    ) -> Processed:
        self.ledger.ingest(
            Verdict(
                repo,
                pr,
                head,
                out.verdict,
                provider if provider in {"codex", "claude"} else "claude",
                run_id,
                provider or "unknown",
                blocking=out.blocking,
                summary=out.text[-SUMMARY_LIMIT:],
            )
        )
        approve = out.verdict == "approve" or (out.verdict == "changes" and out.blocking == 0)
        if not approve:
            self.ledger.mark_blocked(repo, pr, head, why=f"{run_id} {out.verdict} blocking={out.blocking}")
            if round_no >= 2 and out.blocking:
                self._escalate(repo, pr, head, "round_still_blocking", f"round {round_no} blocking={out.blocking}")
                return Processed(run_id, repo, pr, head, out.verdict, Decision("escalate", "round_still_blocking"))
            return Processed(run_id, repo, pr, head, out.verdict, Decision("skip", "blocking_verdict"))
        if not self.ledger.state(repo, pr, head)[1] and not self.ledger.is_blocked(repo, pr, head):
            self._escalate(repo, pr, head, "rubber_stamp", "approve without any review evidence")
            return Processed(run_id, repo, pr, head, out.verdict, Decision("escalate", "rubber_stamp"))
        live = self._port.live(repo, pr)
        holds = self.holds()
        decision = arm_gate(repo, pr, head, live, holds=holds, blocked=self.ledger.is_blocked(repo, pr, live.head_sha))
        if decision.action != "arm" or self.dry_run:
            return Processed(run_id, repo, pr, head, out.verdict, decision)
        outcome = self._port.arm(repo, pr, head, holds=holds)
        self.ledger.record_arm(repo, pr, head, armed=outcome.armed, detail=outcome.detail)
        return Processed(run_id, repo, pr, head, out.verdict, decision, armed=outcome.armed)

    def _escalate(self, repo: str, pr: int, head: str, reason: str, detail: str) -> None:
        if self.dry_run:  # not recorded, so a later live cycle still notifies
            log.info("review-arm [dry-run] would escalate %s#%s @%s: %s (%s)", repo, pr, head[:8], reason, detail)
        elif self.ledger.record_escalation(repo, pr, head, reason):
            self._port.escalate(repo, pr, head, reason, detail)
