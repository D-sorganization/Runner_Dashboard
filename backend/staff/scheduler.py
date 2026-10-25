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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from coordination import claims as coordination_claims
from dashboard_config import HUB_URL
from staff import claim_guard, consolidation, verification
from staff.budget import BudgetGuard
from staff.exclusions import ExclusionsList
from staff.holds import HoldsList
from staff.roles import RoleSpec
from staff.runner import RunRequest, StaffRunner, get_runner
from staff.schedule import DEFAULT_TZ, in_window, next_fire, parse_cron
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.scheduler")

STATE_FILE = "staff_schedule_state.json"
LEADER_LEASE_STATE_KEY = "__leader_lease__"
LEADER_CHAIN_ENV = "STAFF_SCHEDULER_LEADER_CHAIN"
LEADER_TTL_SECONDS_ENV = "STAFF_SCHEDULER_LEASE_TTL_SECONDS"
LEADER_AUTHORITY_URL_ENV = "STAFF_SCHEDULER_LEASE_AUTHORITY_URL"
LEADER_HTTP_TIMEOUT_SECONDS = 5.0
SCHEDULED_PROMPT = (
    "Scheduled {title} pass on {repo}. Follow your playbook for this repository: pick the highest-value items you "
    "can finish safely in this one run and ship them as a ready pull request armed with automerge_guard (pr_lifecycle: "
    "arm_and_exit). If nothing qualifies, open no PR and say why in the STAFF_RESULT line."
)
ISSUE_REMEDIATOR_ROLE = "issue-remediator"
ISSUE_REMEDIATOR_SKIP_LABELS = frozenset(
    {
        "blocked",
        "do-not-automate",
        "judgement:contested",
        "judgement:design",
        "measurement",
        "research",
    }
)
_PRIORITY_LABELS = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def scheduled_repo(repos: tuple[str, ...] | list[str], now: datetime) -> str:
    """Rotate a scheduled role through its repos, one per calendar day (first repo when none rotate)."""
    return repos[now.toordinal() % len(repos)] if repos else ""


def fetch_open_issues(repo: str) -> tuple[list[dict[str, Any]], str | None]:
    """Best-effort open-issue fetch for one repository (pull requests are filtered out)."""
    repo_full = claim_guard.full_repo(repo)
    try:
        payload = claim_guard._gh_cli(f"/repos/{repo_full}/issues?state=open&per_page=100")  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    if not isinstance(payload, list):
        return [], "unexpected response shape"
    issues = [row for row in payload if isinstance(row, dict) and row.get("pull_request") is None]
    return issues, None


def fetch_issue_claim_status(repo: str, issue: int) -> dict[str, Any]:
    """Claim status from coordination scripts; fail-closed reasons are surfaced to callers."""
    try:
        return coordination_claims.check(repo, issue)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "held": None, "reason": f"claim check failed: {exc}"}


def _label_names(issue: dict[str, Any]) -> set[str]:
    labels = issue.get("labels") if isinstance(issue, dict) else []
    names = set()
    for label in labels or []:
        if isinstance(label, dict):
            name = str(label.get("name") or "").strip().lower()
        else:
            name = str(label).strip().lower()
        if name:
            names.add(name)
    return names


def _skip_label_reason(role: str, labels: set[str]) -> str:
    if role != ISSUE_REMEDIATOR_ROLE:
        return ""
    if "do-not-automate" in labels:
        return "skip label: do-not-automate"
    matched = sorted(name for name in labels if name in ISSUE_REMEDIATOR_SKIP_LABELS)
    if matched:
        return f"skip labels: {', '.join(matched)}"
    return ""


def _priority_key(issue: dict[str, Any], labels: set[str]) -> tuple[int, int, int]:
    priority = min((_PRIORITY_LABELS[name] for name in labels if name in _PRIORITY_LABELS), default=2)
    quick_win_penalty = 0 if "quick-win" in labels else 1
    issue_number = int(issue.get("number") or 0)
    return priority, quick_win_penalty, issue_number


REQUESTED_BY = "scheduler"
VERIFY_RECHECK_SECONDS = 300.0  # how often unverified staff runs are re-checked against GitHub (#1516)


def state_path() -> Path:
    return Path(os.environ.get("STAFF_SCHEDULE_STATE", str(_config_dir() / STATE_FILE))).expanduser()


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _leader_chain() -> tuple[str, ...]:
    raw = os.environ.get(LEADER_CHAIN_ENV, "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


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

    # ── leader lease ─────────────────────────────────────────────────────
    def _lease_ttl_seconds(self) -> float:
        raw = os.environ.get(LEADER_TTL_SECONDS_ENV, "").strip()
        if raw:
            try:
                ttl = float(raw)
                if ttl > 0:
                    return ttl
            except ValueError:
                log.warning("staff scheduler: ignored bad %s=%r", LEADER_TTL_SECONDS_ENV, raw)
        return max(self.tick_seconds * 3, self.tick_seconds + 1)

    def _lease_authority_url(self) -> str:
        raw = os.environ.get(LEADER_AUTHORITY_URL_ENV, "").strip()
        if raw:
            return raw.rstrip("/")
        return (HUB_URL or "").rstrip("/")

    def acquire_leader_lease(
        self, machine: str, *, now: datetime | None = None, ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        """Acquire/renew this scheduler lease on the local authority state file.

        Precondition: ``machine`` is a non-empty fleet display name.
        Postcondition: result includes ``granted`` and current ``holder``.
        """
        chain = _leader_chain()
        now = now or self._clock()
        if not chain:
            return {
                "granted": True,
                "holder": machine,
                "reason": "leader lease disabled",
                "expires_at": None,
                "chain": [],
            }
        if machine not in chain:
            return {
                "granted": False,
                "holder": "",
                "reason": f"{machine} not in leader chain",
                "expires_at": None,
                "chain": list(chain),
            }
        ttl = ttl_seconds if ttl_seconds is not None and ttl_seconds > 0 else self._lease_ttl_seconds()
        with self._lock:
            entry = self._load_state().setdefault(LEADER_LEASE_STATE_KEY, {})
            if not isinstance(entry, dict):
                entry = {}
                self._load_state()[LEADER_LEASE_STATE_KEY] = entry
            holder = str(entry.get("holder") or "")
            expires_at = _parse_iso(str(entry.get("expires_at") or ""))
            acquired_at = str(entry.get("acquired_at") or "")
            ranks = {name: idx for idx, name in enumerate(chain)}
            holder_rank = ranks.get(holder, 10_000)
            requester_rank = ranks.get(machine, 10_000)
            active = expires_at is not None and expires_at > now
            granted = False
            reason = ""
            if holder == machine:
                granted = True
                reason = "renewed"
            elif (not active) or not holder:
                granted = True
                reason = "acquired"
            elif requester_rank < holder_rank:
                granted = True
                reason = "preempted by higher-priority host"
            else:
                reason = f"held by {holder}"

            if granted:
                expires = now + timedelta(seconds=ttl)
                entry["holder"] = machine
                entry["acquired_at"] = acquired_at or now.isoformat()
                if holder != machine:
                    entry["acquired_at"] = now.isoformat()
                entry["renewed_at"] = now.isoformat()
                entry["expires_at"] = expires.isoformat()
                entry["ttl_seconds"] = ttl
                entry["chain"] = list(chain)
                self._save_state()
                holder = machine
                expires_at = expires
            return {
                "granted": granted,
                "holder": holder,
                "reason": reason,
                "expires_at": _iso(expires_at),
                "chain": list(chain),
            }

    def _acquire_remote_leader_lease(self, machine: str, now: datetime, ttl_seconds: float) -> dict[str, Any]:
        authority = self._lease_authority_url()
        if not authority:
            return self.acquire_leader_lease(machine, now=now, ttl_seconds=ttl_seconds)
        body = json.dumps({"machine": machine, "ttl_seconds": ttl_seconds}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        token = os.environ.get("HUB_FLEET_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(f"{authority}/api/staff/schedule/leader-lease", data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=LEADER_HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            return {
                "granted": False,
                "holder": "",
                "reason": f"leader lease authority unavailable: {exc}",
                "expires_at": None,
                "chain": list(_leader_chain()),
            }
        if not isinstance(payload, dict):
            return {
                "granted": False,
                "holder": "",
                "reason": "leader lease authority returned non-object payload",
                "expires_at": None,
                "chain": list(_leader_chain()),
            }
        return payload

    def _lease_allows_firing(self, now: datetime) -> bool:
        chain = _leader_chain()
        if not chain:
            return True
        if self.runner.machine not in chain:
            return False
        decision = self._acquire_remote_leader_lease(self.runner.machine, now, self._lease_ttl_seconds())
        if not bool(decision.get("granted", False)):
            holder = str(decision.get("holder") or "<none>")
            reason = str(decision.get("reason") or "lease denied")
            log.info("staff scheduler: lease denied on %s (holder=%s) - %s", self.runner.machine, holder, reason)
            return False
        return True

    def leader_status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or self._clock()
        chain = _leader_chain()
        entry = self._load_state().get(LEADER_LEASE_STATE_KEY, {})
        if not isinstance(entry, dict):
            entry = {}
        holder = str(entry.get("holder") or "")
        expires_at = _parse_iso(str(entry.get("expires_at") or ""))
        active = bool(holder) and expires_at is not None and expires_at > now
        return {
            "configured": bool(chain),
            "chain": list(chain),
            "holder": holder,
            "acquired_at": str(entry.get("acquired_at") or ""),
            "renewed_at": str(entry.get("renewed_at") or ""),
            "expires_at": _iso(expires_at),
            "active": active,
            "local_holder": active and holder == self.runner.machine,
        }

    def _pick_issue_target(self, role: RoleSpec, now: datetime) -> tuple[str, int] | tuple[None, None]:
        repos = tuple(role.repos)
        if not repos:
            return None, None
        start = now.toordinal() % len(repos)
        rotated = list(repos[start:]) + list(repos[:start])
        candidates: list[tuple[tuple[int, int, int], str, int]] = []
        reasons: list[str] = []
        for repo in rotated:
            issues, fetch_error = fetch_open_issues(repo)
            if fetch_error:
                reasons.append(f"{repo}: issue lookup failed ({fetch_error})")
                continue
            if not issues:
                reasons.append(f"{repo}: no open issues")
                continue
            repo_reason = "no eligible issues"
            for issue in issues:
                issue_number = int(issue.get("number") or 0)
                if issue_number <= 0:
                    continue
                labels = _label_names(issue)
                skip_reason = _skip_label_reason(role.name, labels)
                if skip_reason:
                    repo_reason = skip_reason
                    continue
                claim = fetch_issue_claim_status(repo, issue_number)
                if not claim.get("available", False):
                    repo_reason = f"claim check unavailable ({claim.get('reason') or 'unknown'})"
                    continue
                if claim.get("held"):
                    repo_reason = f"held ({claim.get('reason') or 'claimed elsewhere'})"
                    continue
                guard_reason = claim_guard.scheduler_blocker(repo, issue_number, None, role.name)
                if guard_reason:
                    repo_reason = guard_reason
                    continue
                candidates.append((_priority_key(issue, labels), repo, issue_number))
            if repo_reason:
                reasons.append(f"{repo}: {repo_reason}")
        if candidates:
            _, repo, issue_number = min(candidates, key=lambda row: row[0])
            return repo, issue_number
        joined = "; ".join(reasons[:4]) if reasons else "no candidate repositories"
        log.info("staff scheduler: issue-remediator target selection found no eligible issues (%s)", joined)
        return None, None

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
                repo = scheduled_repo(role.repos, now)
                issue_number: int | None = None
                if role.name == ISSUE_REMEDIATOR_ROLE:
                    selected_repo, selected_issue = self._pick_issue_target(role, now)
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
                decision = self._decider(role, repo)
                prompt = SCHEDULED_PROMPT.format(title=role.title, repo=repo or "the fleet")
                if issue_number:
                    prompt = f"{prompt} Target issue #{issue_number} for this run."
                req = RunRequest(
                    role=name,
                    repo=repo,
                    issue=issue_number,
                    prompt=prompt,
                    requested_by=REQUESTED_BY,
                    consolidation=decision,
                )
                claimed = claim_guard.scheduler_blocker(repo, req.issue, req.pr, name) if repo else ""
                if claimed:  # fail closed: a claimed (or unreadable) target is skipped with the reason recorded (#2562)
                    entry["last_reason"] = f"skipped {due}: {claimed}"
                    log.info("staff scheduler: %s slot %s skipped - %s", name, due, claimed)
                    decisions.append({"role": name, "slot": due, "fired": False, "reason": claimed})
                    continue
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
                fired: dict[str, Any] = {"role": name, "slot": due, "fired": True, "run_id": rec.id}
                if decision:
                    fired["consolidation"] = decision
                decisions.append(fired)
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
