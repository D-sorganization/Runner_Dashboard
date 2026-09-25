"""Barb follow-up engine: detect stalled, failed, blocked, and waiting work (SC-C4, Issue #1327).

Periodic idempotent sweep acts on stuck work, retries or re-routes automatically,
and escalates to Barb's conversation thread only when human action is needed.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from fleet_events import FleetEvent, get_event_store
from push import send_push
from staff.audit import record_audit
from staff.conversations import ConversationStore, get_conversation_store
from staff.roles import load_roles
from staff.store import RunRecord, RunStore, get_run_store
from staff.work_items import (
    TERMINAL_STATES,
    WorkItemRecord,
    WorkItemStore,
    get_work_item_store,
)

log = logging.getLogger("dashboard.staff.followup")
DEFAULT_SWEEP_INTERVAL_SECONDS = int(
    os.environ.get("BARB_SWEEP_INTERVAL_SECONDS", "300")
)
DEFAULT_WATCHDOG_MISSED_INTERVALS = 2
WATCHDOG_EVENT_KIND = "barb_followup_watchdog"


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "items_scanned": self.items_scanned,
            "followups": [f.to_dict() for f in self.followups],
            "actions_count": self.actions_count,
            "digest": self.digest.to_dict(),
        }


def _mk_record(
    item_id: str, cond: str, act: str, detail: dict[str, Any], ts: str
) -> FollowupRecord:
    return FollowupRecord(
        f"flw-{uuid.uuid4().hex[:10]}", item_id, "work_item", cond, act, detail, ts
    )


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
    ) -> None:
        self.run_store = run_store or get_run_store()
        self.work_item_store = work_item_store or get_work_item_store()
        self.conversation_store = conversation_store or get_conversation_store()
        self.event_store = event_store or get_event_store()
        self.sweep_interval_seconds = max(30, sweep_interval_seconds)

        self._lock = threading.RLock()
        self._last_sweep_at: datetime | None = None
        self._last_followup: dict[str, float] = {}
        self._followup_history: dict[str, list[FollowupRecord]] = {}
        self._counters: dict[str, int] = {"retried": 0, "rerouted": 0, "escalated": 0}

    @property
    def last_sweep_at(self) -> datetime | None:
        with self._lock:
            return self._last_sweep_at

    def get_followup_records(self, target_id: str) -> list[FollowupRecord]:
        with self._lock:
            return list(self._followup_history.get(target_id, []))

    def _get_barb_thread_id(self) -> str:
        threads = self.conversation_store.list_threads(status="open", limit=50)
        found = next(
            (t for t in threads if "barb" in [p.lower() for p in t.participants]), None
        )
        if not found:
            found = self.conversation_store.create_thread(
                title="Barb", kind="direct", participants=["barb", "user"]
            )
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
            thresh = float(
                DEFAULT_WATCHDOG_MISSED_INTERVALS * self.sweep_interval_seconds
            )
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
                try:
                    send_push(
                        topic="staff.escalation",
                        title="Barb Watchdog Alert",
                        body=detail,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed sending watchdog push: %s", exc)
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
        ts, iso = dt.timestamp(), dt.isoformat().replace("+00:00", "Z")
        with self._lock:
            self._last_sweep_at = dt

        followups: list[FollowupRecord] = []
        counts: dict[str, int] = {}
        items = self.work_item_store.list_work_items(limit=100)
        roles = load_roles()

        for item in items:
            if item.state in TERMINAL_STATES:
                continue
            if (
                ts - self._last_followup.get(item.id, float("-inf"))
                < self.sweep_interval_seconds
            ):
                continue
            rec = self._process_item(item, dt, roles)
            if rec:
                followups.append(rec)
                counts[rec.action_taken] = counts.get(rec.action_taken, 0) + 1
                with self._lock:
                    self._last_followup[item.id] = ts
                    self._followup_history.setdefault(item.id, []).append(rec)
                    ckey = {
                        "retry": "retried",
                        "reroute": "rerouted",
                        "escalate": "escalated",
                    }.get(rec.action_taken, rec.action_taken)
                    if ckey in self._counters:
                        self._counters[ckey] += 1

        return SweepResult(
            iso, len(items), followups, counts, self.get_daily_digest(now=dt)
        )

    def _process_item(
        self, item: WorkItemRecord, now: datetime, valid_roles: dict[str, Any]
    ) -> FollowupRecord | None:
        runs = item.links.get("runs", [])
        if runs:
            r = self.run_store.get_run(runs[-1])
            if r is not None:
                if r.status == "failed" and r.failure_class == "auth_expired":
                    return self._handle_auth_expired(item, r, now)
                if r.status == "stalled" or (
                    r.status == "failed" and r.failure_class == "stalled"
                ):
                    return self._handle_stalled_or_failed_run(item, r, now, "stalled")
                if r.status == "failed":
                    return self._handle_stalled_or_failed_run(item, r, now, "failed")

        if item.state in ("waiting_on_user", "needs_input"):
            return self._handle_needs_input(item, now)

        known = set(valid_roles.keys()) | {"operator", "admin", "user"}
        if item.owner_role and item.owner_role not in known:
            tgt = (
                "librarian"
                if "librarian" in valid_roles
                else next(iter(valid_roles), "operator")
            )
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

    def _handle_stalled_or_failed_run(
        self, item: WorkItemRecord, run: RunRecord, now: datetime, condition: str
    ) -> FollowupRecord:
        now_iso = now.isoformat().replace("+00:00", "Z")
        if run.attempt < run.max_attempts:
            new_run_id = f"{run.id}-r{run.attempt + 1}"
            retry_run = RunRecord(
                id=new_run_id,
                role=run.role,
                provider=run.provider,
                model=run.model,
                machine=run.machine,
                repo=run.repo,
                target_kind=run.target_kind,
                target_ref=run.target_ref,
                prompt=run.prompt,
                status="queued",
                attempt=run.attempt + 1,
                max_attempts=run.max_attempts,
                retry_of=run.id,
                created_at=now_iso,
            )
            self.run_store.create_run(retry_run)
            self.work_item_store.add_link(item.id, "runs", new_run_id)
            detail = {
                "run_id": run.id,
                "new_run_id": new_run_id,
                "attempt": retry_run.attempt,
            }
            _audit("barb_followup_retry", item.id, item.thread_id, detail)
            return _mk_record(item.id, condition, "retry", detail, now_iso)

        self.work_item_store.transition_state(
            item.id,
            "escalated",
            actor="barb",
            reason=f"Run {run.id} {condition} after {run.attempt} attempts",
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
        try:
            send_push(
                topic="staff.escalation",
                title=f"Barb Escalation: {item.title}",
                body=f"Run {run.id} {condition} after {run.attempt} attempts.",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Push send failed: %s", exc)

        detail = {"run_id": run.id, "attempts": run.attempt}
        _audit("barb_followup_escalate", item.id, item.thread_id, detail)
        return _mk_record(item.id, condition, "escalate", detail, now_iso)

    def _handle_auth_expired(
        self, item: WorkItemRecord, run: RunRecord, now: datetime
    ) -> FollowupRecord:
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

    def _handle_needs_input(
        self, item: WorkItemRecord, now: datetime
    ) -> FollowupRecord:
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

    def _handle_overdue_item(
        self, item: WorkItemRecord, now: datetime
    ) -> FollowupRecord:
        now_iso = now.isoformat().replace("+00:00", "Z")
        detail = {"expected_by": item.expected_by}
        _audit("barb_followup_overdue_ping", item.id, item.thread_id, detail)
        return _mk_record(item.id, "overdue", "overdue_ping", detail, now_iso)

    def get_daily_digest(self, now: datetime | None = None) -> FollowupDigest:
        """Calculate digest counts for the day."""
        cur = now or datetime.now(UTC)
        start = (
            cur.replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        now_iso = cur.isoformat().replace("+00:00", "Z")

        items = self.work_item_store.list_work_items(limit=500)
        closed = sum(1 for it in items if it.state in ("done", "cancelled"))
        still_open = sum(1 for it in items if it.state not in TERMINAL_STATES)
        escalated = sum(1 for it in items if it.state == "escalated")

        with self._lock:
            retried = self._counters.get("retried", 0)
            rerouted = self._counters.get("rerouted", 0)

        return FollowupDigest(
            closed, retried, rerouted, escalated, still_open, start, now_iso
        )


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
