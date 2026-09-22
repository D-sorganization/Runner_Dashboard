"""In-process staff scheduler (issue #1196).

Every ``tick_seconds`` the scheduler walks the dispatchable roles that carry a
``schedule`` (five-field cron in ``America/Los_Angeles``) and submits a run
when the next slot after the role's cursor has passed and every gate opens:

  1. the role's ``window`` (``start``/``end``, overnight allowed) contains now;
  2. no active hold matches the role (``staff.holds``);
  3. the role has no queued/preparing/running run already;
  4. the daily budget allows another run (``staff.budget``).

A handled slot — fired or skipped — moves the cursor to *now*, so a scheduler
that was asleep for a day fires at most once when it wakes. The cursor and
last fire time per role persist in ``<config dir>/staff_schedule_state.json``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from staff.budget import BudgetGuard
from staff.holds import HoldsList
from staff.roles import RoleSpec
from staff.runner import RunRequest, StaffRunner, get_runner
from staff.schedule import DEFAULT_TZ, in_window, next_fire, parse_cron
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.scheduler")

STATE_FILE = "staff_schedule_state.json"
SCHEDULED_PROMPT = "Scheduled run"
REQUESTED_BY = "scheduler"


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
    ) -> None:
        assert tick_seconds > 0, "tick_seconds must be positive"  # noqa: S101
        self.runner = runner
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

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="staff-scheduler", daemon=True)
            self._thread.start()
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
            self._stop.wait(self.tick_seconds)

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

    def _cursor(self, role: str, now: datetime) -> datetime:
        entry = self._load_state().setdefault(role, {})
        raw = entry.get("cursor")
        if raw:
            return datetime.fromisoformat(raw)
        entry["cursor"] = now.isoformat()  # never fire slots that predate first sight of the role
        self._save_state()
        return now

    # ── evaluation ───────────────────────────────────────────────────────
    def evaluate(self, role: RoleSpec, now: datetime) -> dict[str, Any]:
        """Gate report for one role: what would happen if its slot were due right now."""
        entry = self._load_state().get(role.name, {})
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
        }
        if role.schedule:
            try:
                out["next_fire"] = _iso(next_fire(parse_cron(role.schedule), self._cursor(role.name, now), self._tz))
            except ValueError as exc:
                out["schedule_error"] = str(exc)
        hold = self.holds.blocking(role.name, role.repos[0] if role.repos else "")
        out["hold"] = hold.text if hold else None
        active = next((r for r in self.runner.store.active_runs() if r.role == role.name), None)
        out["active_run"] = active.id if active else None
        out["budget_ok"], out["budget_reason"] = self.budget.can_run(role)
        return out

    def _blocker(self, report: dict[str, Any]) -> str:
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

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Handle every due slot once. Returns one decision per due role."""
        now = now or self._clock()
        decisions: list[dict[str, Any]] = []
        with self._lock:
            for name, role in sorted(self.runner.roles().items()):
                if not (role.schedule and role.dispatchable):
                    continue
                report = self.evaluate(role, now)
                due = report["next_fire"]
                if due is None or datetime.fromisoformat(due) > now:
                    continue
                blocker = self._blocker(report)
                entry = self._load_state().setdefault(name, {})
                entry["cursor"] = now.isoformat()
                if blocker:
                    entry["last_reason"] = f"skipped {due}: {blocker}"
                    log.info("staff scheduler: %s slot %s skipped — %s", name, due, blocker)
                    decisions.append({"role": name, "slot": due, "fired": False, "reason": blocker})
                    continue
                repo = role.repos[0] if role.repos else ""
                req = RunRequest(role=name, repo=repo, prompt=SCHEDULED_PROMPT, requested_by=REQUESTED_BY)
                try:
                    rec = self.runner.submit(req)
                except ValueError as exc:
                    entry["last_reason"] = f"skipped {due}: {exc}"
                    log.warning("staff scheduler: %s slot %s rejected by runner — %s", name, due, exc)
                    decisions.append({"role": name, "slot": due, "fired": False, "reason": str(exc)})
                    continue
                entry["last_fired"] = now.isoformat()
                entry["last_reason"] = f"fired {due} as {rec.id}"
                log.info("staff scheduler: %s slot %s → %s", name, due, rec.id)
                decisions.append({"role": name, "slot": due, "fired": True, "run_id": rec.id})
            if decisions:
                self._save_state()
        return decisions

    def status(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Per-role gate report for ``GET /api/staff/schedule`` (scheduled roles first)."""
        now = now or self._clock()
        with self._lock:
            reports = [self.evaluate(role, now) for _, role in sorted(self.runner.roles().items()) if role.dispatchable]
        return sorted(reports, key=lambda r: (r["schedule"] is None, r["role"]))


_scheduler: StaffScheduler | None = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> StaffScheduler:
    """Process-wide scheduler bound to the process-wide runner."""
    global _scheduler  # noqa: PLW0603
    with _scheduler_lock:
        if _scheduler is None:
            runner = get_runner()
            _scheduler = StaffScheduler(runner, HoldsList(roles_loader=runner.roles), BudgetGuard(runner.store))
        return _scheduler


def reset_scheduler() -> None:
    global _scheduler  # noqa: PLW0603
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
        _scheduler = None
