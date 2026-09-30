"""Barb follow-up engine: detect stalled, failed, blocked, and waiting work (SC-C4, Issue #1327).

Periodic idempotent sweep acts on stuck work, retries or re-routes automatically,
and escalates to Barb's conversation thread only when human action is needed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, astuple, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import push
from fleet_events import FleetEvent, get_event_store
from staff.audit import record_audit
from staff.conversation_models import ActionProposalRecord
from staff.conversations import ConversationStore, get_conversation_store
from staff.decision_sla import apply_decision_defaults, overdue_decisions
from staff.followup_ledger import FollowupLedger, LedgerRecord, SweepBacklog, stamp
from staff.roles import load_roles
from staff.store import RunRecord, RunStore, get_run_store
from staff.work_items import (
    TERMINAL_STATES,
    WorkItemRecord,
    WorkItemStore,
    active_cursor,
    get_work_item_store,
)

log = logging.getLogger("dashboard.staff.followup")
DEFAULT_SWEEP_INTERVAL_SECONDS = int(os.environ.get("BARB_SWEEP_INTERVAL_SECONDS", "300"))
DEFAULT_WATCHDOG_MISSED_INTERVALS = 2
WATCHDOG_EVENT_KIND: Final = "barb_followup_watchdog"


@dataclass(frozen=True, slots=True)
class FollowupRecord:
    id: str
    target_id: str
    target_kind: str
    condition: str
    action_taken: str
    detail: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FollowupDigest:
    closed: int
    retried: int
    rerouted: int
    escalated: int
    still_open: int
    period_start: str
    period_end: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SweepResult:
    timestamp: str
    items_scanned: int
    followups: list[FollowupRecord]
    actions_count: dict[str, int]
    digest: FollowupDigest
    backlog: SweepBacklog | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "items_scanned": self.items_scanned,
            "followups": [f.to_dict() for f in self.followups],
            "actions_count": self.actions_count,
            "digest": self.digest.to_dict(),
            "backlog": self.backlog.to_dict() if self.backlog else None,
        }


def _mk_record(
    item_id: str, cond: str, act: str, detail: dict[str, Any], ts: str, kind: str = "work_item"
) -> FollowupRecord:
    return FollowupRecord(f"flw-{uuid.uuid4().hex[:10]}", item_id, kind, cond, act, detail, ts)


def _audit(action: str, target: str, thread_id: str, detail: dict[str, Any]) -> None:
    record_audit(
        action=action,
        principal="barb",
        target=target,
        thread_id=thread_id,
        detail=detail,
    )


class FollowupEngine:
    """Coordinates periodic idempotent sweeps across work items and runs."""

    def __init__(
        self,
        run_store: RunStore | None = None,
        work_item_store: WorkItemStore | None = None,
        conversation_store: ConversationStore | None = None,
        event_store: Any | None = None,
        sweep_interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
        runner: Any | None = None,
        holds: Any | None = None,
        ledger: FollowupLedger | None = None,
    ) -> None:
        self.run_store = run_store or get_run_store()
        self.work_item_store = work_item_store or get_work_item_store()
        self.conversation_store = conversation_store or get_conversation_store()
        self.event_store = event_store or get_event_store()
        self.sweep_interval_seconds = max(30, sweep_interval_seconds)
        # Retries start on the staff runner through staff.retry (#1797); both resolve lazily.
        self._runner_override = runner
        self._holds_override = holds

        # Claims, history and counters persist in the work-item database (BR-04, #1798).
        self.ledger = ledger or FollowupLedger(self.work_item_store.db_path)
        self._worker = f"followup-{uuid.uuid4().hex[:8]}"
        self._lock = threading.RLock()
        self._last_sweep_at: datetime | None = None
        self.last_backlog: SweepBacklog | None = None

    @property
    def last_sweep_at(self) -> datetime | None:
        with self._lock:
            return self._last_sweep_at

    def get_followup_records(self, target_id: str) -> list[FollowupRecord]:
        return [FollowupRecord(*astuple(r)) for r in self.ledger.records_for(target_id)]

    def _persist(self, rec: FollowupRecord) -> None:
        self.ledger.record(LedgerRecord(*astuple(rec)))

    def _active_items(self, page: int = 200) -> Iterator[WorkItemRecord]:
        """Every non-terminal work item, earliest deadline first, in keyset pages."""
        after = None
        while batch := self.work_item_store.list_active_page(after=after, limit=page):
            yield from batch
            after = active_cursor(batch[-1])

    def _get_barb_thread_id(self) -> str:
        threads = self.conversation_store.list_threads(status="open", limit=50)
        found = next((t for t in threads if "barb" in [p.lower() for p in t.participants]), None)
        if not found:
            found = self.conversation_store.create_thread(title="Barb", kind="direct", participants=["barb", "user"])
        return found.id

    def check_watchdog(self, now: datetime | None = None) -> dict[str, Any]:
        """Verify sweep loop has not stalled; alert if >= 2 intervals missed."""
        cur = now or datetime.now(UTC)
        cur_ts = cur.timestamp()
        with self._lock:
            if self._last_sweep_at is None:
                return {
                    "ok": True,
                    "alert_fired": False,
                    "reason": "no_previous_sweep",
                    "sweep_interval_seconds": self.sweep_interval_seconds,
                }
            elapsed = max(0.0, cur_ts - self._last_sweep_at.timestamp())
            thresh = float(DEFAULT_WATCHDOG_MISSED_INTERVALS * self.sweep_interval_seconds)
            if elapsed > thresh:
                missed = int(elapsed // self.sweep_interval_seconds)
                detail = f"Follow-up engine missed {missed} intervals ({int(elapsed)}s elapsed)"
                log.warning("Barb watchdog alert: %s", detail)
                ev = FleetEvent(
                    ts=int(cur_ts * 1000),
                    severity="critical",
                    kind=WATCHDOG_EVENT_KIND,
                    title="Barb follow-up engine stalled",
                    detail=detail,
                    node=os.environ.get("HOSTNAME", "Desk"),
                )
                self.event_store.record(ev)
                push.notify("staff.escalation", "Barb Watchdog Alert", detail)
                return {
                    "ok": False,
                    "alert_fired": True,
                    "missed_intervals": missed,
                    "elapsed_seconds": elapsed,
                    "threshold_seconds": thresh,
                }
            return {
                "ok": True,
                "alert_fired": False,
                "elapsed_seconds": elapsed,
                "threshold_seconds": thresh,
            }

    def sweep(self, now: datetime | None = None) -> SweepResult:
        """Run a single idempotent follow-up sweep over work items and runs."""
        dt = now or datetime.now(UTC)
        started, iso = time.monotonic(), dt.isoformat().replace("+00:00", "Z")
        now_s, next_s = stamp(dt), stamp(dt + timedelta(seconds=self.sweep_interval_seconds))
        with self._lock:
            self._last_sweep_at = dt

        followups: list[FollowupRecord] = []
        counts: dict[str, int] = {}
        backlog = SweepBacklog()
        roles = load_roles()

        # Earliest deadline first, every page: new arrivals cannot starve older work (BR-04).
        for item in self._active_items():
            backlog.observe(item.expected_by, iso, dt)
            if not self.ledger.claim(item.id, "work_item", now_s, next_s, self._worker):
                continue
            rec = self._process_item(item, dt, roles)
            if rec is None:
                self.ledger.release(item.id, now_s, self._worker)
                continue
            self._persist(rec)
            followups.append(rec)
            counts[rec.action_taken] = counts.get(rec.action_taken, 0) + 1

        for rec in self._sweep_decisions(dt, now_s, next_s, backlog):
            self._persist(rec)
            followups.append(rec)
            counts[rec.action_taken] = counts.get(rec.action_taken, 0) + 1

        backlog.duration_ms = int((time.monotonic() - started) * 1000)
        self.last_backlog = backlog
        return SweepResult(iso, backlog.active, followups, counts, self.get_daily_digest(now=dt), backlog)

    def _process_item(self, item: WorkItemRecord, now: datetime, valid_roles: dict[str, Any]) -> FollowupRecord | None:
        runs = item.links.get("runs", [])
        if runs:
            r = self.run_store.get_run(runs[-1])
            if r is not None:
                if r.status == "failed" and r.failure_class == "auth_expired":
                    return self._handle_auth_expired(item, r, now)
                if r.status == "stalled" or (r.status == "failed" and r.failure_class == "stalled"):
                    return self._handle_stalled_or_failed_run(item, r, now, "stalled")
                if r.status == "failed":
                    return self._handle_stalled_or_failed_run(item, r, now, "failed")

        if item.state in ("waiting_on_user", "needs_input"):
            return self._handle_needs_input(item, now)

        known = set(valid_roles.keys()) | {"operator", "admin", "user"}
        if item.owner_role and item.owner_role not in known:
            tgt = "librarian" if "librarian" in valid_roles else next(iter(valid_roles), "operator")
            self.work_item_store.update_work_item(item.id, owner_role=tgt)
            _audit(
                "barb_followup_reroute",
                item.id,
                item.thread_id,
                {"old": item.owner_role, "new": tgt},
            )
            return _mk_record(
                item.id,
                "wrong_owner",
                "reroute",
                {"previous_role": item.owner_role, "new_role": tgt},
                now.isoformat().replace("+00:00", "Z"),
            )

        now_iso = now.isoformat().replace("+00:00", "Z")
        if item.expected_by and item.expected_by < now_iso:
            return self._handle_overdue_item(item, now)
        return None

    def _sweep_decisions(self, now: datetime, now_s: str, next_s: str, backlog: SweepBacklog) -> list[FollowupRecord]:
        """Decision SLA (WP-2.6, #1607): apply each overdue default, ping overdue decisions without one.

        A default that was refused stays with the owner and is pinged like a silent item. Each
        overdue proposal is claimed in the ledger first, so one worker acts on it per interval.
        """
        now_iso = now.isoformat().replace("+00:00", "Z")
        overdue = overdue_decisions(self.conversation_store, now)
        backlog.decisions_overdue = len(overdue)
        recent = {p.id for p in overdue if not self.ledger.claim(p.id, "proposal", now_s, next_s, self._worker)}
        records: list[FollowupRecord] = []
        refused: set[str] = set()
        for o in apply_decision_defaults(self.conversation_store, now=now, skip=recent):
            if o.outcome == "refused":
                refused.add(o.proposal_id)
                continue
            detail = {"default_if_silent": o.applied, "outcome": o.outcome, "detail": o.detail}
            rec = _mk_record(o.proposal_id, "decision_overdue", "default_applied", detail, now_iso, "proposal")
            records.append(rec)
        for prop in overdue:
            if prop.id in recent or (prop.default_if_silent and prop.id not in refused):
                continue
            records.append(self._ping_decision(prop, now_iso))
        return records

    def _ping_decision(self, prop: ActionProposalRecord, now_iso: str) -> FollowupRecord:
        msg = (
            f"⏰ **Barb → Owner: Decision Overdue**\n\n"
            f"Proposal `{prop.id}` (`{prop.action}`) was due for a decision by {prop.decide_by}. "
            f"Please approve or deny it."
        )
        self.conversation_store.add_message(
            thread_id=self._get_barb_thread_id(),
            author_kind="role",
            author="barb",
            body_md=msg,
            kind="text",
            meta={"proposal_id": prop.id, "decide_by": prop.decide_by},
        )
        detail = {"decide_by": prop.decide_by, "action": prop.action}
        _audit("barb_followup_decision_ping", prop.id, prop.thread_id, detail)
        return _mk_record(prop.id, "decision_overdue", "decision_ping", detail, now_iso, "proposal")

    def _runner(self) -> Any:
        if self._runner_override is None:
            from staff.runner import get_runner

            self._runner_override = get_runner()
        return self._runner_override

    def _holds(self) -> Any:
        if self._holds_override is None:
            from staff.holds import HoldsList

            self._holds_override = HoldsList()
        return self._holds_override

    def _handle_stalled_or_failed_run(
        self, item: WorkItemRecord, run: RunRecord, now: datetime, condition: str
    ) -> FollowupRecord:
        """Retry the run through :func:`staff.retry.launch_retry`, or escalate (#1797).

        A stalled original is cancelled and marked failed first, so it cannot finish
        alongside its retry. The retry runs on a worker with the original's thread,
        work-item and origin links; the class, attempt, budget and hold gates decide.
        """
        from staff.retry import launch_retry

        now_iso = now.isoformat().replace("+00:00", "Z")
        runner = self._runner()
        if run.status != "failed":
            runner.cancel(run.id)
            self.run_store.update_run(run.id, status="failed", failure_class="stalled", ended_at=now_iso)
            self.run_store.append_event(run.id, "stalled", "reconciled by Barb's follow-up before a retry")
        decision = launch_retry(
            runner, run, source="followup", holds=self._holds(), allow_classes=frozenset({"stalled"})
        )
        if decision.launched or decision.reason == "already claimed":
            if decision.run_id not in item.links.get("runs", []):
                self.work_item_store.add_link(item.id, "runs", decision.run_id)
            detail = {"run_id": run.id, "new_run_id": decision.run_id, "attempt": run.attempt + 1}
            if not decision.launched:
                # Another path (post-execution retry, a concurrent sweep) owns this attempt.
                return _mk_record(item.id, condition, "retry_pending", detail, now_iso)
            _audit("barb_followup_retry", item.id, item.thread_id, detail)
            return _mk_record(item.id, condition, "retry", detail, now_iso)

        self.work_item_store.transition_state(
            item.id,
            "escalated",
            actor="barb",
            reason=f"Run {run.id} {condition} after {run.attempt} attempts; not retried: {decision.reason}",
        )
        body = (
            f"🚨 **Barb Follow-Up Escalation**\n\n"
            f"Work item **{item.title}** (`{item.id}`) has been escalated. "
            f"Run `{run.id}` ({run.role}) encountered repeated {condition} failures "
            f"after {run.attempt} attempts. Repo: `{run.repo}` | Target: `{run.target_ref}`"
        )
        self.conversation_store.add_message(
            thread_id=self._get_barb_thread_id(),
            author_kind="role",
            author="barb",
            body_md=body,
            kind="text",
            meta={"escalation_type": condition, "item_id": item.id, "run_id": run.id},
        )
        push.notify(
            "staff.escalation",
            f"Barb Escalation: {item.title}",
            f"Run {run.id} {condition} after {run.attempt} attempts.",
        )

        detail = {"run_id": run.id, "attempts": run.attempt, "retry_refused": decision.reason}
        _audit("barb_followup_escalate", item.id, item.thread_id, detail)
        return _mk_record(item.id, condition, "escalate", detail, now_iso)

    def _handle_auth_expired(self, item: WorkItemRecord, run: RunRecord, now: datetime) -> FollowupRecord:
        now_iso = now.isoformat().replace("+00:00", "Z")
        msg = (
            f"⚠️ **Action Required: Authentication Expired**\n\n"
            f"Work item **{item.title}** cannot proceed because credentials for provider "
            f"`{run.provider}` expired on run `{run.id}`. Re-authenticate in Settings."
        )
        self.conversation_store.add_message(
            thread_id=self._get_barb_thread_id(),
            author_kind="role",
            author="barb",
            body_md=msg,
            kind="text",
            meta={"provider": run.provider, "run_id": run.id},
        )
        detail = {"provider": run.provider, "run_id": run.id}
        _audit("barb_followup_auth_alert", item.id, item.thread_id, detail)
        return _mk_record(item.id, "auth_expired", "auth_alert", detail, now_iso)

    def _handle_needs_input(self, item: WorkItemRecord, now: datetime) -> FollowupRecord:
        now_iso = now.isoformat().replace("+00:00", "Z")
        msg = (
            f"❓ **Barb → Owner: Input Needed**\n\n"
            f"Role `{item.owner_role}` needs input on **{item.title}** (`{item.id}`). "
            f"Please respond to resume progress."
        )
        self.conversation_store.add_message(
            thread_id=self._get_barb_thread_id(),
            author_kind="role",
            author="barb",
            body_md=msg,
            kind="text",
            meta={"item_id": item.id, "owner_role": item.owner_role},
        )
        detail = {"item_title": item.title, "owner_role": item.owner_role}
        _audit("barb_followup_ask_owner", item.id, item.thread_id, detail)
        return _mk_record(item.id, "needs_input", "ask_owner", detail, now_iso)

    def _handle_overdue_item(self, item: WorkItemRecord, now: datetime) -> FollowupRecord:
        now_iso = now.isoformat().replace("+00:00", "Z")
        detail = {"expected_by": item.expected_by}
        _audit("barb_followup_overdue_ping", item.id, item.thread_id, detail)
        return _mk_record(item.id, "overdue", "overdue_ping", detail, now_iso)

    def get_daily_digest(self, now: datetime | None = None) -> FollowupDigest:
        """Calculate digest counts for the day."""
        cur = now or datetime.now(UTC)
        start = cur.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        now_iso = cur.isoformat().replace("+00:00", "Z")

        items = self.work_item_store.list_work_items(limit=500)
        closed = sum(1 for it in items if it.state in ("done", "cancelled"))
        still_open = sum(1 for it in items if it.state not in TERMINAL_STATES)
        escalated = sum(1 for it in items if it.state == "escalated")

        actions = self.ledger.action_counts(start)
        retried, rerouted = actions.get("retry", 0), actions.get("reroute", 0)
        return FollowupDigest(closed, retried, rerouted, escalated, still_open, start, now_iso)


_engine_instance: FollowupEngine | None = None
_engine_lock = threading.Lock()


def get_followup_engine() -> FollowupEngine:
    global _engine_instance  # noqa: PLW0603
    with _engine_lock:
        if _engine_instance is None:
            _engine_instance = FollowupEngine()
        return _engine_instance


def reset_followup_engine() -> None:
    global _engine_instance  # noqa: PLW0603
    with _engine_lock:
        _engine_instance = None
