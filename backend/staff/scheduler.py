"""In-process staff scheduler (issue #1196).

Every ``tick_seconds`` the scheduler walks the dispatchable roles that carry a
``schedule`` (five-field cron in ``America/Los_Angeles``) and submits a run
when the next slot after the role's cursor has passed and every gate opens:

  1. the role's ``window`` (``start``/``end``, overnight allowed) contains now;
  2. no active *schedule*-kind hold matches the role (``staff.holds``); seeded
     guardrail holds never block (owner decision, #1726);
  3. the role has no queued/preparing/running run already;
  4. the daily budget allows another run (``staff.budget``).

A handled slot — fired or skipped — moves the cursor to *now*, so a scheduler
that was asleep fires at most once when it wakes. A cursor that is more than one
full slot behind (scheduler disabled for weeks) is reseeded to *now*, so the
next fire is the next real slot rather than a stale one. The cursor and
last fire time per role persist in ``<config dir>/staff_schedule_state.json``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from staff import claim_guard, consolidation, verification
from staff.budget import BudgetGuard
from staff.exclusions import ExclusionsList
from staff.holds import HoldsList
from staff.roles import RoleSpec
from staff.runner import RunRequest, StaffRunner, get_runner
from staff.schedule import DEFAULT_TZ, in_window, next_fire, parse_cron
from staff.scheduler_issue_pick import (
    ISSUE_REMEDIATOR_ROLE,
    fetch_issue_claim_status,
    fetch_open_issues,
    pick_issue_target,
    scheduled_repo,
)
from staff.scheduler_leader_lease import (
    acquire_leader_lease as _lease_acquire_leader_lease,
)
from staff.scheduler_leader_lease import (
    leader_chain,
    lease_allows_firing,
    lease_ttl_seconds,
)
from staff.scheduler_leader_lease import (
    leader_status as _lease_leader_status,
)
from staff.scheduler_startup import mark_scheduler_started, skip_due_slot_during_startup
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.scheduler")

STATE_FILE = "staff_schedule_state.json"
SCHEDULED_PROMPT = (
    "Scheduled {title} pass on {repo}. Follow your playbook for this repository: pick the highest-value items you "
    "can finish safely in this one run and ship them as a ready pull request armed with automerge_guard (pr_lifecycle: "
    "arm_and_exit). If nothing qualifies, open no PR and say why in the STAFF_RESULT line."
)
REQUESTED_BY = "scheduler"
VERIFY_RECHECK_SECONDS = 300.0  # how often unverified staff runs are re-checked against GitHub (#1516)


def state_path() -> Path:
    return Path(os.environ.get("STAFF_SCHEDULE_STATE", str(_config_dir() / STATE_FILE))).expanduser()


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


class StaffScheduler:
    """Background ticker that turns role schedules into ``runner.submit`` calls."""

    def __init__(
        self,
        runner: StaffRunner,
        holds: HoldsList,
        budget: BudgetGuard,
        tick_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        state_file: Path | None = None,
        tz: str = DEFAULT_TZ,
        decider: Callable[[RoleSpec, str], dict[str, Any] | None] = consolidation.decide,
    ) -> None:
        assert tick_seconds > 0, "tick_seconds must be positive"  # noqa: S101
        self.runner = runner
        self._decider = decider  # PR-consolidation decision per due slot (#1213); injectable for tests
        self.holds = holds
        self.budget = budget
        self.tick_seconds = tick_seconds
        self._tz = tz
        self._clock = clock or (lambda: datetime.now(ZoneInfo(tz)))
        self._state_file = state_file
        self._state: dict[str, dict[str, Any]] | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_verify_at = 0.0

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="staff-scheduler", daemon=True)
            self._thread.start()
        mark_scheduler_started()
        log.info("staff scheduler started (tick %.0fs)", self.tick_seconds)

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("staff scheduler tick failed")
            self.recheck_verification()
            self._stop.wait(self.tick_seconds)

    def recheck_verification(self) -> None:
        """Re-verify this node's recent unchecked or unverified runs, at most every VERIFY_RECHECK_SECONDS (#1516)."""
        now = time.monotonic()
        if now < self._next_verify_at:
            return
        self._next_verify_at = now + VERIFY_RECHECK_SECONDS
        try:
            verification.recheck_runs(self.runner.store, machine=self.runner.machine, opens_pr=self.runner.opens_pr)
        except Exception:  # noqa: BLE001
            log.exception("staff run verification recheck failed")

    # ── state ────────────────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self._state_file or state_path()

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if self._state is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            except (OSError, ValueError) as exc:
                log.warning("staff scheduler: state file %s unreadable (%s); starting fresh", self.path, exc)
                raw = {}
            self._state = {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
        return self._state

    def _save_state(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state or {}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _cursor(self, role: str, now: datetime, schedule: str | None = None) -> datetime:
        entry = self._load_state().setdefault(role, {})
        raw = entry.get("cursor")
        if raw:
            cursor = datetime.fromisoformat(raw)
            if schedule and self._is_stale(schedule, cursor, now):
                # Missed more than one full slot (e.g. scheduler disabled for weeks): reseed so the
                # next fire is a real upcoming slot instead of replaying a stale one.
                entry["cursor"] = now.isoformat()
                self._save_state()
                return now
            return cursor
        entry["cursor"] = now.isoformat()  # never fire slots that predate first sight of the role
        self._save_state()
        return now

    def _is_stale(self, schedule: str, cursor: datetime, now: datetime) -> bool:
        """True when at least two slots after ``cursor`` have already passed (one missed slot still fires once)."""
        try:
            spec = parse_cron(schedule)
            first = next_fire(spec, cursor, self._tz)
            return next_fire(spec, first, self._tz) <= now
        except ValueError:
            return False

    # ── leader lease (fencing logic lives in scheduler_leader_lease.py; #cap) ──
    def _lease_ttl_seconds(self) -> float:
        return lease_ttl_seconds(self.tick_seconds)

    def acquire_leader_lease(
        self, machine: str, *, now: datetime | None = None, ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        """Acquire/renew this scheduler lease on the local authority state file.

        Precondition: ``machine`` is a non-empty fleet display name.
        Postcondition: result includes ``granted`` and current ``holder``.
        """
        now = now or self._clock()
        ttl = ttl_seconds if ttl_seconds is not None and ttl_seconds > 0 else self._lease_ttl_seconds()
        with self._lock:
            return _lease_acquire_leader_lease(self._load_state, self._save_state, machine, now=now, ttl_seconds=ttl)

    def _lease_allows_firing(self, now: datetime) -> bool:
        return lease_allows_firing(self.acquire_leader_lease, self.runner.machine, now, self._lease_ttl_seconds())

    def leader_status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or self._clock()
        return _lease_leader_status(self._load_state, leader_chain(), self.runner.machine, now)

    # ── evaluation ───────────────────────────────────────────────────────
    def evaluate(self, role: RoleSpec, now: datetime) -> dict[str, Any]:
        """Gate report for one role: what would happen if its slot were due right now."""
        entry = self._load_state().get(role.name, {})
        enabled = entry.get("enabled", True)
        out: dict[str, Any] = {
            "role": role.name,
            "schedule": role.schedule,
            "window": role.window,
            "next_fire": None,
            "in_window": in_window(role.window, now, self._tz),
            "hold": None,
            "active_run": None,
            "budget_ok": True,
            "budget_reason": "",
            "last_fired": entry.get("last_fired"),
            "last_reason": entry.get("last_reason", ""),
            "enabled": enabled,
        }
        if role.schedule:
            try:
                out["next_fire"] = _iso(
                    next_fire(parse_cron(role.schedule), self._cursor(role.name, now, role.schedule), self._tz)
                )
            except ValueError as exc:
                out["schedule_error"] = str(exc)
        hold = self.holds.blocking(role.name, role.repos[0] if role.repos else "")
        out["hold"] = hold.text if hold else (None if enabled else "schedule disabled for role")
        active = next((r for r in self.runner.store.active_runs() if r.role == role.name), None)
        out["active_run"] = active.id if active else None
        out["budget_ok"], out["budget_reason"] = self.budget.can_run(role)
        return out

    def _blocker(self, report: dict[str, Any]) -> str:
        if not report.get("enabled", True):
            return "schedule disabled for role"
        if report.get("schedule_error"):
            return f"bad schedule: {report['schedule_error']}"
        if not report["in_window"]:
            return "outside run window"
        if report["hold"]:
            return f"hold: {report['hold']}"
        if report["active_run"]:
            return f"run {report['active_run']} still active"
        if not report["budget_ok"]:
            return f"budget: {report['budget_reason']}"
        return ""

    def role_model_override(self, role_name: str) -> str | None:
        """Current runtime model override for ``role_name``, or ``None`` when unset (epic #3295 S2)."""
        with self._lock:
            raw = self._load_state().get(role_name, {}).get("model_override")
            return str(raw) if raw else None

    def set_role_model_override(self, role_name: str, model: str | None) -> None:
        """Persist (or clear, when ``model`` is ``None``) a role's runtime model override."""
        with self._lock:
            entry = self._load_state().setdefault(role_name, {})
            if model:
                entry["model_override"] = model
            else:
                entry.pop("model_override", None)
            self._save_state()

    def set_role_schedule_enabled(self, role_name: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable scheduling for a specific role (SC-D6, Issue #1320)."""
        with self._lock:
            entry = self._load_state().setdefault(role_name, {})
            entry["enabled"] = enabled
            self._save_state()
            now = self._clock()
            role = self.runner.roles().get(role_name)
            if role:
                return self.evaluate(role, now)
            return {"role": role_name, "enabled": enabled}

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Handle every due slot once. Returns one decision per due role."""
        now = now or self._clock()
        decisions: list[dict[str, Any]] = []
        if not self._lease_allows_firing(now):
            return decisions
        pending_fires: list[dict[str, Any]] = []
        with self._lock:
            for name, role in sorted(self.runner.roles().items()):
                if not (role.schedule and role.dispatchable):
                    continue
                report = self.evaluate(role, now)
                due = report["next_fire"]
                if due is None or datetime.fromisoformat(due) > now:
                    continue
                if skip_due_slot_during_startup(name):
                    log.info("staff scheduler: %s slot %s deferred — hub startup grace", name, due)
                    continue
                blocker = self._blocker(report)
                entry = self._load_state().setdefault(name, {})
                entry["cursor"] = now.isoformat()
                if blocker:
                    entry["last_reason"] = f"skipped {due}: {blocker}"
                    log.info("staff scheduler: %s slot %s skipped — %s", name, due, blocker)
                    decisions.append({"role": name, "slot": due, "fired": False, "reason": blocker})
                    continue
                repo = scheduled_repo(role.repos, now)
                issue_number: int | None = None
                if role.name == ISSUE_REMEDIATOR_ROLE:
                    selected_repo, selected_issue = pick_issue_target(
                        role,
                        now,
                        fetch_open=fetch_open_issues,
                        fetch_claim=fetch_issue_claim_status,
                    )
                    if not selected_repo or not selected_issue:
                        reason = "no eligible issues in scheduled repositories"
                        entry["last_reason"] = f"skipped {due}: {reason}"
                        decisions.append({"role": name, "slot": due, "fired": False, "reason": reason})
                        continue
                    repo = selected_repo
                    issue_number = selected_issue
                repo_hold = self.holds.blocking(name, repo)
                if repo_hold:
                    entry["last_reason"] = f"skipped {due}: hold: {repo_hold.text}"
                    decisions.append({"role": name, "slot": due, "fired": False, "reason": f"hold: {repo_hold.text}"})
                    continue
                excluded = ExclusionsList().blocking(repo) if repo else None
                if excluded:
                    reason = f"excluded: {excluded.owner}: {excluded.reason}"
                    entry["last_reason"] = f"skipped {due}: {reason}"
                    log.info("staff scheduler: %s slot %s skipped - %s (%s)", name, due, reason, excluded.id)
                    decisions.append({"role": name, "slot": due, "fired": False, "reason": reason})
                    continue
                pending_fires.append(
                    {
                        "name": name,
                        "role": role,
                        "due": due,
                        "repo": repo,
                        "issue_number": issue_number,
                    }
                )
            if decisions:
                self._save_state()
        for pending in pending_fires:
            decisions.append(self._fire_pending_slot(now, **pending))
        if pending_fires:
            with self._lock:
                self._save_state()
        return decisions

    def _fire_pending_slot(
        self,
        now: datetime,
        *,
        name: str,
        role: RoleSpec,
        due: str,
        repo: str | None,
        issue_number: int | None,
    ) -> dict[str, Any]:
        """Run slow consolidation/claim/submit work without holding the scheduler lock (#3526)."""
        decision = self._decider(role, repo or "")
        prompt = SCHEDULED_PROMPT.format(title=role.title, repo=repo or "the fleet")
        if issue_number:
            prompt = f"{prompt} Target issue #{issue_number} for this run."
        req = RunRequest(
            role=name,
            repo=repo or "",
            issue=issue_number,
            prompt=prompt,
            requested_by=REQUESTED_BY,
            consolidation=decision,
        )
        claimed = claim_guard.scheduler_blocker(repo, req.issue, req.pr, name) if repo else ""
        with self._lock:
            entry = self._load_state().setdefault(name, {})
            if claimed:
                entry["last_reason"] = f"skipped {due}: {claimed}"
                log.info("staff scheduler: %s slot %s skipped - %s", name, due, claimed)
                return {"role": name, "slot": due, "fired": False, "reason": claimed}
            try:
                rec = self.runner.submit(req)
            except ValueError as exc:
                entry["last_reason"] = f"skipped {due}: {exc}"
                log.warning("staff scheduler: %s slot %s rejected by runner — %s", name, due, exc)
                return {"role": name, "slot": due, "fired": False, "reason": str(exc)}
            entry["last_fired"] = now.isoformat()
            entry["last_reason"] = f"fired {due} as {rec.id}"
        log.info("staff scheduler: %s slot %s → %s", name, due, rec.id)
        fired: dict[str, Any] = {"role": name, "slot": due, "fired": True, "run_id": rec.id}
        if decision:
            fired["consolidation"] = decision
        return fired

    def status(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Per-role gate report for ``GET /api/staff/schedule`` (scheduled roles first)."""
        now = now or self._clock()
        with self._lock:
            roles = [(n, r) for n, r in sorted(self.runner.roles().items()) if r.dispatchable]
        reports = [self.evaluate(role, now) for _, role in roles]
        return sorted(reports, key=lambda r: (r["schedule"] is None, r["role"]))


_scheduler: StaffScheduler | None = None
_scheduler_lock = threading.Lock()
_built_scheduler: StaffScheduler | None = None  # the instance get_scheduler() constructed (#3622)


def get_scheduler() -> StaffScheduler:
    """Process-wide scheduler bound to the process-wide runner.

    A scheduler built here follows the runner: once the process-wide runner (and its
    store) is replaced it is rebuilt instead of keeping a budget guard on a closed store
    (#3622). An injected ``_scheduler`` is returned as is.
    """
    global _scheduler, _built_scheduler  # noqa: PLW0603
    with _scheduler_lock:
        runner = get_runner()
        was_running = False
        if _scheduler is not None and _scheduler is _built_scheduler and _scheduler.runner is not runner:
            was_running = _scheduler.running
            _scheduler.stop()
            _scheduler = None
        if _scheduler is None:
            _scheduler = StaffScheduler(runner, HoldsList(roles_loader=runner.roles), BudgetGuard(runner.store))
            _built_scheduler = _scheduler
            if was_running:
                _scheduler.start()
        return _scheduler


def reset_scheduler() -> None:
    global _scheduler  # noqa: PLW0603
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
        _scheduler = None
