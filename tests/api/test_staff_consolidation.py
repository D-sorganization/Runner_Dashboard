"""PR-consolidation strategy for staff runs (epic #1192, issue #1213, companion RM#1690).

``evaluate`` is exercised as a pure function; the fetchers are faked through
``decide``'s injection points; the runner, scheduler and dispatch route are
driven with the same fake-CLI pattern as ``test_staff_runner.py`` so the
prompt paragraph, ``strategy_mode`` and the parsed ``outcome`` are checked
end-to-end without any provider CLI or GitHub access.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff import adapters as adapters_mod
from staff import budget as budget_mod
from staff import consolidation
from staff import holds as holds_mod
from staff import roles as roles_mod
from staff import runner as runner_mod
from staff import scheduler as scheduler_mod
from staff import store as store_mod
from staff import workspace as workspace_mod
from staff.runner import RunRequest

_XHR = {"X-Requested-With": "XMLHttpRequest"}
LA = ZoneInfo("America/Los_Angeles")

_FAKE_CLI = """
import json, sys
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "folding"}]}}))
usage = {"input_tokens": 10, "output_tokens": 5}
result = "STAFF_RESULT: Consolidated 12 PRs into #1801 (https://example.test/pull/1801)"
print(json.dumps({"type": "result", "result": result, "usage": usage, "total_cost_usd": 0.01}))
"""

_STRATEGY = {"consolidate_when": {"open_prs": 6, "utilisation_pct": 70}}


def _role(strategy: dict[str, Any] | None = _STRATEGY, **extra: Any) -> roles_mod.RoleSpec:
    data: dict[str, Any] = {"name": "pr-remediator", "title": "PR Remediator", "repos": ["UpstreamDrift"], **extra}
    if strategy is not None:
        data["strategy"] = strategy
    return roles_mod.parse_role(data, "<inline>")


# ── role schema ──────────────────────────────────────────────────────────
@pytest.mark.unit
def test_role_strategy_is_optional_additive_and_exposed() -> None:
    plain = _role(strategy=None)
    assert plain.strategy == {} and plain.to_dict()["strategy"] == {}
    role = _role({"consolidate_when": {"open_prs": 6, "utilisation_pct": 70, "future": 1}, "other": "x"})
    assert role.strategy["consolidate_when"]["open_prs"] == 6
    assert role.to_dict()["strategy"] == role.strategy
    assert _role(strategy="not-a-mapping").strategy == {}  # type: ignore[arg-type]


@pytest.mark.unit
def test_threshold_parses_ints_and_ignores_junk() -> None:
    assert consolidation.threshold(_role()) == {"open_prs": 6, "utilisation_pct": 70}
    assert consolidation.threshold(_role(strategy=None)) is None
    assert consolidation.threshold(_role({"consolidate_when": {}})) is None
    assert consolidation.threshold(_role({"consolidate_when": "6"})) is None
    assert consolidation.threshold(_role({"consolidate_when": {"open_prs": "8", "utilisation_pct": True}})) == {
        "open_prs": 8
    }
    assert consolidation.threshold(_role({"consolidate_when": {"open_prs": "many", "utilisation_pct": -5}})) == {
        "utilisation_pct": 0
    }


# ── evaluate (pure) ──────────────────────────────────────────────────────
@pytest.mark.unit
def test_evaluate_consolidates_only_when_every_threshold_is_met() -> None:
    role = _role()
    on = consolidation.evaluate(role, "UpstreamDrift", 12, 85)
    assert on["mode"] == "consolidate"
    assert on["threshold"] == {"open_prs": 6, "utilisation_pct": 70}
    assert "open PRs 12 >= 6" in on["reason"] and "utilisation 85% >= 70%" in on["reason"]
    assert consolidation.evaluate(role, "UpstreamDrift", 6, 70)["mode"] == "consolidate"  # boundary inclusive
    few = consolidation.evaluate(role, "UpstreamDrift", 3, 95)
    assert few["mode"] == "serial" and few["reason"] == "open PRs 3 < 6"
    idle = consolidation.evaluate(role, "UpstreamDrift", 30, 10)
    assert idle["mode"] == "serial" and idle["reason"] == "utilisation 10% < 70%"
    both = consolidation.evaluate(role, "UpstreamDrift", 0, 0)
    assert both["mode"] == "serial" and "open PRs 0 < 6; utilisation 0% < 70%" == both["reason"]


@pytest.mark.unit
def test_evaluate_single_threshold_and_no_strategy() -> None:
    prs_only = _role({"consolidate_when": {"open_prs": 4}})
    assert consolidation.evaluate(prs_only, "Tools", 4, 0)["mode"] == "consolidate"
    assert consolidation.evaluate(prs_only, "Tools", 3, 100)["mode"] == "serial"
    none = consolidation.evaluate(_role(strategy=None), "Tools", 50, 100)
    assert none["mode"] == "serial" and none["threshold"] == {} and "no consolidate_when" in none["reason"]
    with pytest.raises(AssertionError):
        consolidation.evaluate(prs_only, "Tools", -1, 0)


@pytest.mark.unit
def test_prompt_paragraph_states_mode_reason_exclusions_and_ci_rule() -> None:
    text = consolidation.prompt_paragraph({"mode": "consolidate", "reason": "open PRs 12 >= 6"})
    assert text.startswith("Consolidation mode: consolidate — open PRs 12 >= 6.")
    assert "into ONE PR" in text and "exclusions: draft, do-not-merge, do-not-automate, claim:local" in text
    assert "bot snapshot PRs that delete main lines" in text
    assert "Never cancel or re-run other PRs' CI." in text
    serial = consolidation.prompt_paragraph({"mode": "serial", "reason": "open PRs 3 < 6"})
    assert serial.startswith("Consolidation mode: serial — open PRs 3 < 6.")
    assert "one at a time" in serial and "Never cancel or re-run other PRs' CI." in serial


# ── decide with injected fetchers ────────────────────────────────────────
@pytest.mark.unit
def test_decide_uses_fakes_skips_when_not_applicable_and_fails_safe() -> None:
    role = _role()
    seen: list[str] = []

    def prs(repo: str) -> int:
        seen.append(repo)
        return 9

    out = consolidation.decide(role, "UpstreamDrift", pr_counter=prs, utilisation=lambda: 80)
    assert out is not None and out["mode"] == "consolidate" and seen == ["UpstreamDrift"]
    assert consolidation.decide(role, "", pr_counter=prs, utilisation=lambda: 80) is None
    assert consolidation.decide(_role(strategy=None), "UpstreamDrift", pr_counter=prs, utilisation=lambda: 80) is None

    def boom(_repo: str) -> int:
        raise RuntimeError("gh: HTTP 502")

    down = consolidation.decide(role, "UpstreamDrift", pr_counter=boom, utilisation=lambda: 80)
    assert down is not None and down["mode"] == "serial"
    assert down["reason"].startswith("inputs unavailable") and "502" in down["reason"]
    assert down["threshold"] == {"open_prs": 6, "utilisation_pct": 70}


# ── outcome parsing ──────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("STAFF_RESULT: consolidated 12 PRs into #1801", "consolidated 12 PRs into #1801"),
        ("STAFF_RESULT: Consolidated 1 PR into #7 and pushed", "consolidated 1 PR into #7"),
        ("STAFF_RESULT: CONSOLIDATED  3  prs INTO #42", "consolidated 3 PRs into #42"),
        ("STAFF_RESULT: opened PR #12", ""),
        ("", ""),
    ],
)
def test_parse_outcome(text: str, expected: str) -> None:
    assert consolidation.parse_outcome(text) == expected


# ── real fetchers with stubbed I/O ───────────────────────────────────────
@pytest.mark.unit
def test_open_pr_count_counts_non_draft_across_paginated_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = (
        json.dumps([{"number": 1, "draft": False}, {"number": 2, "draft": True}]) + "\n" + json.dumps([{"number": 3}])
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=pages, stderr="")

    monkeypatch.setattr(consolidation.subprocess, "run", fake_run)
    assert consolidation.open_pr_count("UpstreamDrift") == 2
    assert calls[0][:3] == ["gh", "api", "--paginate"] and calls[0][-1].endswith(
        "/UpstreamDrift/pulls?state=open&per_page=100"
    )
    assert consolidation._concatenated_json("") == []
    with pytest.raises(AssertionError):
        consolidation.open_pr_count("")


@pytest.mark.unit
def test_utilisation_pct_reads_the_orchestrator_capacity_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator_api  # noqa: PLC0415

    monkeypatch.setattr(orchestrator_api, "_capacity_provider", lambda: {"online_runners": 8, "busy_runners": 6})
    assert consolidation.utilisation_pct() == 75

    async def async_provider() -> dict[str, int]:
        await asyncio.sleep(0)
        return {"online_runners": 0, "busy_runners": 0}

    monkeypatch.setattr(orchestrator_api, "_capacity_provider", async_provider)
    assert consolidation.utilisation_pct() == 0
    monkeypatch.setattr(orchestrator_api, "_capacity_provider", lambda: None)
    with pytest.raises(TypeError):
        consolidation.utilisation_pct()


# ── store migration ──────────────────────────────────────────────────────
@pytest.mark.unit
def test_store_adds_strategy_mode_and_outcome_columns_to_an_older_db(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript(store_mod._SCHEMA)
    conn.close()
    store = store_mod.RunStore(db)
    assert {"cost_method", "strategy_mode", "outcome"} <= store.columns()
    rec = store_mod.RunRecord(
        id="run-x", role="r", provider="p", model=None, machine="m", repo="", target_kind="prompt",
        target_ref="", prompt="p", strategy_mode="consolidate",
    )  # fmt: skip
    store.create_run(rec)
    store.update_run("run-x", outcome="consolidated 2 PRs into #9")
    got = store.get_run("run-x")
    assert got is not None and got.strategy_mode == "consolidate" and got.outcome == "consolidated 2 PRs into #9"
    assert got.to_dict()["outcome"] == "consolidated 2 PRs into #9"
    store.close()


# ── runner wiring ────────────────────────────────────────────────────────
@pytest.fixture
def role_dir(tmp_path: Path) -> Path:
    d = tmp_path / "roles"
    d.mkdir()
    (d / "pr-remediator.yml").write_text(
        "\n".join(
            [
                "name: pr-remediator",
                "title: PR Remediator",
                "providers: [fake]",
                "schedule: '0 22 * * *'",
                "repos: [UpstreamDrift]",
                "permissions: {lease: false}",
                "strategy: {consolidate_when: {open_prs: 6, utilisation_pct: 70}}",
            ]
        ),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def staff(tmp_path: Path, role_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[runner_mod.StaffRunner]:
    script = tmp_path / "fake_cli.py"
    script.write_text(_FAKE_CLI, encoding="utf-8")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(role_dir))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path / "nonexistent-repos"))
    fake = adapters_mod.ProviderAdapter(
        provider_id="fake", label="Fake", executable=sys.executable, argv=(str(script), "{prompt}"), json_lines=True
    )
    adapters = {**adapters_mod.ADAPTERS, "fake": fake}
    monkeypatch.setattr(adapters_mod, "ADAPTERS", adapters)
    # Repo-scoped runs would clone and add a git worktree; stub both so the test stays offline.
    monkeypatch.setattr(workspace_mod, "find_repo_checkout", lambda _repo: tmp_path / "checkout")
    monkeypatch.setattr(workspace_mod, "add_worktree", lambda _c, wt, _b: wt.mkdir(parents=True, exist_ok=True))
    store_mod.reset_store()
    runner_mod.reset_runner()
    r = runner_mod.StaffRunner(
        store=store_mod.RunStore(tmp_path / "runs.sqlite3"), adapters=adapters, machine="TestNode"
    )
    monkeypatch.setattr(runner_mod, "_runner", r)
    yield r
    store_mod.reset_store()
    runner_mod.reset_runner()


def _wait(store: store_mod.RunStore, run_id: str, timeout: float = 20.0) -> store_mod.RunRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = store.get_run(run_id)
        assert rec is not None
        if rec.status not in store_mod.ACTIVE_STATUSES:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still active")


@pytest.mark.unit
def test_plan_and_submit_carry_the_decision_and_parse_the_outcome(staff: runner_mod.StaffRunner) -> None:
    decision = {"mode": "consolidate", "reason": "open PRs 12 >= 6", "threshold": {"open_prs": 6}}
    req = RunRequest(role="pr-remediator", repo="UpstreamDrift", prompt="sweep", consolidation=decision)
    plan = staff.plan(req)
    assert plan.to_dict()["consolidation"] == decision
    assert "Consolidation mode: consolidate — open PRs 12 >= 6." in plan.prompt
    assert plan.prompt.index("Consolidation mode") < plan.prompt.index("Fleet rules:")
    bare = staff.plan(RunRequest(role="pr-remediator", repo="UpstreamDrift", prompt="sweep"))
    assert bare.to_dict()["consolidation"] is None and "Consolidation mode" not in bare.prompt

    rec = _wait(staff.store, staff.submit(req).id)
    assert rec.status == "succeeded" and rec.strategy_mode == "consolidate"
    assert rec.outcome == "consolidated 12 PRs into #1801"
    assert "Consolidation mode: consolidate" in rec.prompt
    plain = _wait(staff.store, staff.submit(RunRequest(role="pr-remediator", prompt="no repo")).id)
    assert plain.strategy_mode == "" and plain.outcome == "consolidated 12 PRs into #1801"


# ── scheduler wiring ─────────────────────────────────────────────────────
class _FakeRunner:
    machine = "TestNode"

    def __init__(self, store: store_mod.RunStore, roles: dict[str, roles_mod.RoleSpec]) -> None:
        self.store = store
        self._roles = roles
        self.submitted: list[RunRequest] = []

    def roles(self) -> dict[str, roles_mod.RoleSpec]:
        return self._roles

    def submit(self, req: RunRequest) -> store_mod.RunRecord:
        self.submitted.append(req)
        rec = store_mod.RunRecord(
            id=f"run-{len(self.submitted)}", role=req.role, provider="fake", model=None, machine=self.machine,
            repo=req.repo, target_kind="prompt", target_ref="", prompt=req.prompt,
        )  # fmt: skip
        return self.store.create_run(rec)


@pytest.mark.unit
def test_scheduler_tick_evaluates_the_strategy_before_submitting(tmp_path: Path) -> None:
    store = store_mod.RunStore(tmp_path / "runs.sqlite3")
    roles = {"pr-remediator": _role(schedule="0 22 * * *", providers=["fake"])}
    runner = _FakeRunner(store, roles)
    asked: list[tuple[str, str]] = []

    def decider(role: roles_mod.RoleSpec, repo: str) -> dict[str, Any] | None:
        asked.append((role.name, repo))
        return consolidation.evaluate(role, repo, 8, 90)

    clock = {"now": datetime(2026, 9, 22, 21, 0, tzinfo=LA)}
    holds = holds_mod.HoldsList(tmp_path / "holds.json", roles_loader=dict)
    holds.replace([])
    guard = budget_mod.BudgetGuard(store, alert_sink=lambda *_: None, clock=lambda: clock["now"])
    sched = scheduler_mod.StaffScheduler(
        runner, holds, guard, clock=lambda: clock["now"], state_file=tmp_path / "state.json", decider=decider
    )
    assert sched.tick() == []  # cursor initialised; 22:00 not yet due
    clock["now"] = datetime(2026, 9, 22, 22, 0, 30, tzinfo=LA)
    decisions = sched.tick()
    assert len(decisions) == 1 and decisions[0]["fired"] is True
    assert decisions[0]["consolidation"]["mode"] == "consolidate"
    assert asked == [("pr-remediator", "UpstreamDrift")]
    assert runner.submitted[0].consolidation == decisions[0]["consolidation"]
    store.close()


# ── dispatch route ───────────────────────────────────────────────────────
@pytest.fixture
def client(staff: runner_mod.StaffRunner) -> Iterator[TestClient]:
    from identity import require_fleet_peer, require_orchestrator_peer  # noqa: PLC0415
    from routers import staff as staff_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(staff_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_orchestrator_peer] = lambda: "test-orchestrator"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_dispatch_dry_run_and_submit_report_the_decision(
    client: TestClient, staff: runner_mod.StaffRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(consolidation, "open_pr_count", lambda _repo: 3)
    monkeypatch.setattr(consolidation, "utilisation_pct", lambda: 95)
    monkeypatch.setattr(consolidation, "decide", lambda role, repo: consolidation.evaluate(role, repo, 3, 95))
    body = {"provider": "fake", "repo": "UpstreamDrift", "prompt": "sweep", "dry_run": True}
    resp = client.post("/api/staff/pr-remediator/run", json=body, headers=_XHR)
    assert resp.status_code == 200, resp.text
    plan = resp.json()["plan"]
    assert plan["consolidation"] == {
        "mode": "serial",
        "reason": "open PRs 3 < 6",
        "threshold": {"open_prs": 6, "utilisation_pct": 70},
    }
    assert "Consolidation mode: serial — open PRs 3 < 6." in plan["prompt"]
    assert staff.store.list_runs() == []

    roster = client.get("/api/staff/roster").json()
    role = next(r for r in roster["roles"] if r["name"] == "pr-remediator")
    assert role["strategy"] == {"consolidate_when": {"open_prs": 6, "utilisation_pct": 70}}

    resp = client.post("/api/staff/pr-remediator/run", json={**body, "dry_run": False}, headers=_XHR)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run"]["id"]
    assert resp.json()["run"]["strategy_mode"] == "serial"
    rec = _wait(staff.store, run_id)
    detail = client.get(f"/api/staff/runs/{run_id}").json()["run"]
    assert rec.outcome == detail["outcome"] == "consolidated 12 PRs into #1801"

    # No repo → no evaluation, no paragraph.
    resp = client.post(
        "/api/staff/pr-remediator/run", json={"provider": "fake", "prompt": "x", "dry_run": True}, headers=_XHR
    )
    assert resp.json()["plan"]["consolidation"] is None
