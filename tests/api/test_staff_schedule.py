"""Staff Hub scheduler, run windows, holds and budgets (epic #1192, issue #1196).

No subprocess is ever launched: a fake runner records ``submit`` calls and
writes a ``queued`` row into a temp run store; the scheduler is driven with a
frozen clock through ``tick(now)``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff import budget as budget_mod
from staff import holds as holds_mod
from staff import roles as roles_mod
from staff import scheduler as scheduler_mod
from staff import store as store_mod
from staff.runner import RunRequest
from staff.schedule import in_window, next_fire, parse_cron

LA = ZoneInfo("America/Los_Angeles")
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _la(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=LA)


# ── cron ─────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_parse_cron_star_lists_ranges_steps_and_names() -> None:
    spec = parse_cron("*/15 22,6 1-3 jan,mar sun-tue")
    assert spec.minutes == frozenset({0, 15, 30, 45})
    assert spec.hours == frozenset({6, 22})
    assert spec.days == frozenset({1, 2, 3})
    assert spec.months == frozenset({1, 3})
    assert spec.weekdays == frozenset({0, 1, 2})
    assert parse_cron("0 0 * * 7").weekdays == frozenset({0})  # 7 == Sunday
    assert parse_cron("5/20 * * * *").minutes == frozenset({5, 25, 45})
    assert not parse_cron("* * * * *").days_restricted


@pytest.mark.unit
@pytest.mark.parametrize(
    "expr",
    [
        "",
        "0 22 * *",
        "60 * * * *",
        "0 24 * * *",
        "0 0 0 * *",
        "0 0 * 13 *",
        "0 0 * * 8",
        "a b c d e",
        "10-5 * * * *",
        "*/0 * * * *",
        "0 22 * * * *",
    ],
)
def test_parse_cron_rejects_bad_input(expr: str) -> None:
    with pytest.raises(ValueError):
        parse_cron(expr)


@pytest.mark.unit
def test_next_fire_is_strictly_after_and_in_zone() -> None:
    after = _la(2026, 9, 22, 22, 0, 0)
    nxt = next_fire("0 22 * * *", after)
    assert nxt == _la(2026, 9, 23, 22, 0) and nxt.tzinfo is not None
    assert next_fire("0 22 * * *", _la(2026, 9, 22, 21, 59, 30)) == _la(2026, 9, 22, 22, 0)
    # a naive ``after`` is interpreted in the role zone; a UTC one is converted
    assert next_fire("0 22 * * *", datetime(2026, 9, 22, 21, 0)) == _la(2026, 9, 22, 22, 0)
    utc_after = datetime(2026, 9, 23, 4, 30, tzinfo=ZoneInfo("UTC"))  # 21:30 LA
    assert next_fire("0 22 * * *", utc_after) == _la(2026, 9, 22, 22, 0)


@pytest.mark.unit
def test_next_fire_weekday_dom_or_semantics_and_month_rollover() -> None:
    # dom OR dow when both restricted: 2026-10-01 is a Thursday, the 15th is a Thursday too
    nxt = next_fire("0 9 15 * thu", _la(2026, 9, 30, 12))
    assert nxt == _la(2026, 10, 1, 9, 0)
    # only dow restricted: next Monday
    assert next_fire("30 8 * * mon", _la(2026, 9, 22, 12)).weekday() == 0
    # month rollover across the year boundary
    assert next_fire("0 0 1 1 *", _la(2026, 9, 22)) == _la(2027, 1, 1, 0, 0)
    with pytest.raises(ValueError):
        next_fire("0 0 31 2 *", _la(2026, 1, 1))


@pytest.mark.unit
def test_next_fire_across_dst_transition_keeps_wall_clock() -> None:
    # 2026-11-01 02:00 PDT → PST; a 22:00 schedule still fires at 22:00 wall clock
    nxt = next_fire("0 22 * * *", _la(2026, 10, 31, 23))
    assert (nxt.hour, nxt.minute) == (22, 0) and nxt.date().isoformat() == "2026-11-01"
    assert nxt.utcoffset() == timedelta(hours=-8)


# ── windows ──────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_in_window_overnight_daytime_and_open() -> None:
    night = {"start": "22:00", "end": "06:00"}
    assert in_window(night, _la(2026, 9, 22, 22, 0))
    assert in_window(night, _la(2026, 9, 22, 23, 59))
    assert in_window(night, _la(2026, 9, 23, 2, 30))
    assert not in_window(night, _la(2026, 9, 23, 6, 0))
    assert not in_window(night, _la(2026, 9, 22, 12, 0))
    assert not in_window(night, _la(2026, 9, 22, 21, 59))
    day = {"start": "09:00", "end": "17:00"}
    assert in_window(day, _la(2026, 9, 22, 9, 0)) and not in_window(day, _la(2026, 9, 22, 17, 0))
    assert in_window(None, _la(2026, 9, 22, 3)) and in_window({"start": "", "end": ""}, _la(2026, 9, 22, 3))
    assert in_window({"start": "08:00", "end": "08:00"}, _la(2026, 9, 22, 3))
    # UTC input is converted to the role zone: 05:00Z on the 23rd is 22:00 LA on the 22nd
    assert in_window(night, datetime(2026, 9, 23, 5, 0, tzinfo=ZoneInfo("UTC")))
    with pytest.raises(ValueError):
        in_window({"start": "22", "end": "06:00"}, _la(2026, 9, 22))


# ── holds ────────────────────────────────────────────────────────────────
def _roles() -> dict[str, roles_mod.RoleSpec]:
    return {
        "night-watch": roles_mod.parse_role(
            {
                "name": "night-watch",
                "providers": ["fake"],
                "schedule": "0 22 * * *",
                "window": {"start": "22:00", "end": "06:00"},
                "repos": ["UpstreamDrift"],
                "budget": {"usd_per_run": 3, "usd_per_day": 15},
                "holds": ["no bulk stale-queue cancel", "never merge"],
            }
        ),
        "steward": roles_mod.parse_role({"name": "steward", "providers": ["fake"], "holds": ["Never merge"]}),
        "ad-hoc": roles_mod.parse_role({"name": "ad-hoc", "providers": ["fake"]}),
    }


@pytest.mark.unit
def test_holds_seed_from_roles_persist_and_replace(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "staff_holds.json"
    holds = holds_mod.HoldsList(path, roles_loader=_roles)
    seeded = holds.load()
    assert path.exists()
    by_text = {h.text: h for h in seeded}
    assert set(by_text) == {"no bulk stale-queue cancel", "never merge"}
    assert by_text["never merge"].applies_to == ["night-watch", "steward"]  # merged case-insensitively
    assert all(h.active and h.id.startswith("hold-") for h in seeded)
    # second load reads the file, not the roles
    assert [h.id for h in holds_mod.HoldsList(path, roles_loader=dict).load()] == [h.id for h in seeded]
    replaced = holds.replace([{"text": "freeze deploys", "applies_to": ["*"], "lifted_when": "after #1201"}])
    assert len(replaced) == 1 and replaced[0].lifted_when == "after #1201"
    assert json.loads(path.read_text(encoding="utf-8"))["holds"][0]["text"] == "freeze deploys"
    with pytest.raises(ValueError):
        holds.replace([{"text": ""}])
    with pytest.raises(ValueError):
        holds.replace([{"id": "x", "text": "a"}, {"id": "x", "text": "b"}])
    path.write_text("{not json", encoding="utf-8")
    assert holds.load() == []


@pytest.mark.unit
def test_hold_matches_role_star_repo_and_inactive() -> None:
    star = holds_mod.Hold(id="h1", text="freeze", applies_to=["*"])
    role = holds_mod.Hold(id="h2", text="x", applies_to=["night-watch"])
    repo = holds_mod.Hold(id="h3", text="y", applies_to=["repo:Tools"])
    off = holds_mod.Hold(id="h4", text="z", applies_to=["*"], active=False)
    assert holds_mod.matches(star, "anything")
    assert holds_mod.matches(role, "night-watch") and not holds_mod.matches(role, "steward")
    assert holds_mod.matches(repo, "steward", "Tools") and not holds_mod.matches(repo, "steward", "UpstreamDrift")
    assert not holds_mod.matches(off, "night-watch")


# ── budget ───────────────────────────────────────────────────────────────
class _FakeStore:
    def __init__(self, spend: dict[str, float]) -> None:
        self.spend = spend
        self.queries: list[str] = []

    def spend_by_role_since(self, since_iso: str) -> dict[str, float]:
        self.queries.append(since_iso)
        return dict(self.spend)


@pytest.mark.unit
def test_budget_can_run_thresholds_and_debounce() -> None:
    role = _roles()["night-watch"]  # $15/day, $3/run
    store = _FakeStore({"night-watch": 0.0})
    alerts: list[tuple[str, str, float]] = []
    now = _la(2026, 9, 22, 10)
    guard = budget_mod.BudgetGuard(store, alert_sink=lambda r, m, f: alerts.append((r, m, f)), clock=lambda: now)  # type: ignore[arg-type]
    assert guard.can_run(role) == (True, "$0.00 of $15.00 spent today")
    assert store.queries[-1] == "2026-09-22T07:00:00Z"  # local midnight in UTC
    store.spend["night-watch"] = 11.5  # 76 %
    ok, _ = guard.can_run(role)
    assert ok and [a[1] for a in alerts] == ["75% of daily budget: $11.50 of $15.00"]
    guard.can_run(role)
    assert len(alerts) == 1  # debounced
    store.spend["night-watch"] = 13.0  # 86 %: fits under cap alone, but a $3 run would not
    ok, reason = guard.can_run(role)
    assert not ok and "would exceed" in reason and len(alerts) == 1
    store.spend["night-watch"] = 15.0
    ok, reason = guard.can_run(role)
    assert not ok and "reached" in reason
    assert [a[2] >= 1.0 for a in alerts[1:]] == [True, True]  # 90 % and 100 % fire together at 100 %
    now = now + timedelta(hours=6, minutes=1)
    guard.can_run(role)
    assert len(alerts) == 6  # every threshold re-alerts after the debounce window
    assert guard.can_run(_roles()["ad-hoc"]) == (True, "no daily budget")


# ── scheduler ────────────────────────────────────────────────────────────
class _FakeRunner:
    """Records submissions and writes a queued row so ``active_runs`` sees it."""

    machine = "TestNode"

    def __init__(self, store: store_mod.RunStore, roles: dict[str, roles_mod.RoleSpec]) -> None:
        self.store = store
        self._roles = roles
        self.submitted: list[RunRequest] = []
        self.reject = False

    def roles(self) -> dict[str, roles_mod.RoleSpec]:
        return self._roles

    def submit(self, req: RunRequest) -> store_mod.RunRecord:
        if self.reject:
            raise ValueError("runner said no")
        self.submitted.append(req)
        rec = store_mod.RunRecord(
            id=f"run-{len(self.submitted)}",
            role=req.role,
            provider="fake",
            model=None,
            machine=self.machine,
            repo=req.repo,
            target_kind="prompt",
            target_ref="",
            prompt=req.prompt,
            requested_by=req.requested_by,
        )
        return self.store.create_run(rec)


@pytest.fixture
def sched(tmp_path: Path) -> tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]]:
    store = store_mod.RunStore(tmp_path / "runs.sqlite3")
    runner = _FakeRunner(store, _roles())
    clock = {"now": _la(2026, 9, 22, 21, 0)}
    holds = holds_mod.HoldsList(tmp_path / "holds.json", roles_loader=dict)
    holds.replace([])
    guard = budget_mod.BudgetGuard(store, alert_sink=lambda *_: None, clock=lambda: clock["now"])
    s = scheduler_mod.StaffScheduler(
        runner,
        holds,
        guard,
        tick_seconds=30,
        clock=lambda: clock["now"],
        state_file=tmp_path / "state.json",  # type: ignore[arg-type]
    )
    return s, runner, clock


@pytest.mark.unit
def test_scheduler_fires_once_per_slot_and_persists_state(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]],
) -> None:
    s, runner, clock = sched
    assert s.tick() == [] and runner.submitted == []  # 21:00 — slot not due; cursor anchored
    clock["now"] = _la(2026, 9, 22, 22, 0, 10)
    slot = _la(2026, 9, 22, 22, 0).isoformat()
    assert s.tick() == [{"role": "night-watch", "slot": slot, "fired": True, "run_id": "run-1"}]
    req = runner.submitted[0]
    assert (req.role, req.repo) == ("night-watch", "UpstreamDrift")
    assert req.prompt.startswith("Scheduled ") and "pass on UpstreamDrift." in req.prompt
    assert req.requested_by == "scheduler"
    clock["now"] = _la(2026, 9, 22, 22, 0, 40)
    assert s.tick() == [] and len(runner.submitted) == 1  # same slot, not refired
    state = json.loads(s.path.read_text(encoding="utf-8"))
    assert state["night-watch"]["last_fired"] == _la(2026, 9, 22, 22, 0, 10).isoformat()
    assert "run-1" in state["night-watch"]["last_reason"]
    # a fresh scheduler on the same state file does not refire the handled slot
    s2 = scheduler_mod.StaffScheduler(runner, s.holds, s.budget, clock=lambda: clock["now"], state_file=s.path)  # type: ignore[arg-type]
    assert s2.tick() == []
    # asleep for three days → exactly one run when it wakes
    runner.store.update_run("run-1", status="succeeded")
    clock["now"] = _la(2026, 9, 25, 23, 0)
    assert [d["fired"] for d in s2.tick()] == [True] and len(runner.submitted) == 2
    assert s2.tick() == []


@pytest.mark.unit
def test_scheduler_gates_window_hold_active_run_budget_and_runner_errors(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]],
) -> None:
    s, runner, clock = sched
    role = runner.roles()["night-watch"]
    # window: 22:00 slot handled at 07:00 the next morning is outside 22–06
    s.tick()
    clock["now"] = _la(2026, 9, 23, 7, 0)
    assert s.tick()[0]["reason"] == "outside run window"
    # hold
    s.holds.replace([{"text": "no overnight runs", "applies_to": ["night-watch"]}])
    clock["now"] = _la(2026, 9, 23, 22, 0, 5)
    assert s.tick()[0]["reason"] == "hold: no overnight runs"
    assert s.evaluate(role, clock["now"])["hold"] == "no overnight runs"
    s.holds.replace([{"text": "unrelated", "applies_to": ["steward"]}])
    # active run for the role
    runner.submit(RunRequest(role="night-watch", prompt="manual"))
    clock["now"] = _la(2026, 9, 24, 22, 0, 5)
    assert s.tick()[0]["reason"] == "run run-1 still active"
    runner.store.update_run("run-1", status="succeeded", cost_usd=15.0)
    # budget: $15 spent today (created_at is "now" in UTC, i.e. today) → refused
    clock["now"] = _la(2026, 9, 25, 22, 0, 5)
    runner.store.update_run("run-1", created_at=budget_mod.day_start_iso(clock["now"]))
    assert s.tick()[0]["reason"].startswith("budget: daily budget $15.00 reached")
    runner.store.update_run("run-1", cost_usd=0.0)
    # runner rejects → skipped, slot consumed, no crash
    runner.reject = True
    clock["now"] = _la(2026, 9, 26, 22, 0, 5)
    assert s.tick()[0]["reason"] == "runner said no"
    runner.reject = False
    assert s.tick() == []
    assert len(runner.submitted) == 1  # only the manual run ever got through


@pytest.mark.unit
def test_scheduler_status_reports_every_dispatchable_role_and_bad_cron(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]],
) -> None:
    s, runner, clock = sched
    runner._roles["broken"] = roles_mod.parse_role({"name": "broken", "providers": ["fake"], "schedule": "bad"})
    rows = {r["role"]: r for r in s.status()}
    assert [r["role"] for r in s.status()][:2] == ["broken", "night-watch"]  # scheduled roles first
    nw = rows["night-watch"]
    assert nw["next_fire"] == _la(2026, 9, 22, 22, 0).isoformat() and nw["in_window"] is False
    assert nw["budget_ok"] and nw["hold"] is None and nw["last_fired"] is None
    assert rows["ad-hoc"]["next_fire"] is None and "schedule_error" in rows["broken"]
    assert s.tick() == []  # a bad schedule never raises out of the tick


@pytest.mark.unit
def test_scheduler_thread_start_stop_and_tolerates_tick_errors(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]], monkeypatch: pytest.MonkeyPatch
) -> None:
    s, _, _ = sched
    s.tick_seconds = 0.01
    monkeypatch.setattr(s, "tick", lambda now=None: 1 / 0)
    s.start()
    s.start()  # idempotent
    assert s.running
    s.stop()
    assert s._thread is not None
    s._thread.join(timeout=2)
    assert not s.running


# ── routes ───────────────────────────────────────────────────────────────
@pytest.fixture
def client(
    sched: tuple[scheduler_mod.StaffScheduler, _FakeRunner, dict[str, datetime]], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    from identity import require_fleet_peer, require_orchestrator_peer  # noqa: PLC0415
    from routers import staff_schedule as schedule_router  # noqa: PLC0415

    monkeypatch.setattr(scheduler_mod, "_scheduler", sched[0])
    app = FastAPI()
    app.include_router(schedule_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_orchestrator_peer] = lambda: "test-orchestrator"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    scheduler_mod.reset_scheduler()


@pytest.mark.unit
def test_holds_routes_get_put_validate(client: TestClient) -> None:
    assert client.get("/api/staff/holds").json()["holds"] == []
    body = {"holds": [{"text": "no bulk stale-queue cancel", "applies_to": ["night-watch"]}]}
    r = client.put("/api/staff/holds", json=body, headers=_XHR)
    assert r.status_code == 200, r.text
    saved = r.json()["holds"]
    assert saved[0]["text"] == "no bulk stale-queue cancel" and saved[0]["active"] is True
    assert client.get("/api/staff/holds").json()["holds"] == saved
    assert client.put("/api/staff/holds", json={"holds": [{"text": ""}]}, headers=_XHR).status_code == 422
    dup = {"holds": [{"id": "d", "text": "a"}, {"id": "d", "text": "b"}]}
    assert client.put("/api/staff/holds", json=dup, headers=_XHR).status_code == 422


@pytest.mark.unit
def test_schedule_route_reports_roles(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_SCHEDULER_ENABLED", "0")
    data = client.get("/api/staff/schedule").json()
    assert data["machine"] == "TestNode" and data["enabled"] is False and data["running"] is False
    roles = {r["role"]: r for r in data["roles"]}
    assert roles["night-watch"]["schedule"] == "0 22 * * *" and roles["night-watch"]["next_fire"]
    assert roles["ad-hoc"]["next_fire"] is None


@pytest.mark.unit
def test_start_scheduler_respects_env_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import staff_schedule as schedule_router  # noqa: PLC0415

    started: list[bool] = []

    class _S:
        def start(self) -> None:
            started.append(True)

    monkeypatch.setattr(schedule_router, "get_scheduler", lambda: _S())
    monkeypatch.setenv("STAFF_SCHEDULER_ENABLED", "false")
    schedule_router.start_scheduler()
    assert started == []
    monkeypatch.setenv("STAFF_SCHEDULER_ENABLED", "1")
    schedule_router.start_scheduler()
    assert started == [True]


def test_scheduled_repo_rotates_one_repo_per_day() -> None:
    repos = ("A", "B", "C")
    days = [_la(2026, 9, d, 22, 0) for d in (22, 23, 24, 25)]
    picked = [scheduler_mod.scheduled_repo(repos, d) for d in days]
    assert sorted(picked[:3]) == ["A", "B", "C"] and picked[3] == picked[0]
    assert scheduler_mod.scheduled_repo((), days[0]) == ""


def test_active_holds_feeds_summary_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /api/staff/summary`` reads ``holds.active_holds`` (it was missing, so summary showed none)."""
    path = tmp_path / "holds.json"
    path.write_text(
        json.dumps({"holds": [{"text": "keep", "applies_to": ["barb"]}, {"text": "old", "active": False}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(path))
    assert [h["text"] for h in holds_mod.active_holds()] == ["keep"]
