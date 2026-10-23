"""Unit & API tests for Agent Report Cards, Bean-Counter Ledger & Multi-Barb Continuity."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from routers import staff_barb, staff_outcomes, staff_usage
from staff.barb_continuity import BarbContinuityStore, classify_task_surface
from staff.outcomes import PrFact, full_repo
from staff.report_cards import build_report_cards, wilson_ci_95
from staff.store import RunRecord, RunStore


def _make_run(
    run_id: str,
    *,
    role: str = "issue-remediator",
    provider: str = "claude",
    model: str = "claude-opus-4-6",
    status: str = "succeeded",
    exit_code: int = 0,
    input_tokens: int = 20_000,
    output_tokens: int = 2_000,
    standards_tokens: int = 1_500,
    cost_usd: float = 0.25,
    attempt: int = 1,
    retry_of: str = "",
    failure_class: str = "",
    verification: str = "verified",
    repo: str = "Runner_Dashboard",
    pr_number: int | None = None,
    branch: str = "",
    target_kind: str = "issue",
    target_ref: str = "100",
) -> RunRecord:
    return RunRecord(
        id=run_id,
        role=role,
        provider=provider,
        model=model,
        machine="DeskComputer",
        repo=repo,
        prompt="fix issue",
        status=status,
        created_at="2026-10-22T18:00:00Z",
        started_at="2026-10-22T18:00:05Z",
        ended_at="2026-10-22T18:05:00Z",
        exit_code=exit_code,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        standards_tokens=standards_tokens,
        cost_usd=cost_usd,
        attempt=attempt,
        retry_of=retry_of,
        failure_class=failure_class,
        verification=verification,
        pr_number=pr_number,
        branch=branch,
        target_kind=target_kind,
        target_ref=target_ref,
    )


def test_wilson_ci_95_bounds() -> None:
    assert wilson_ci_95(0, 0) is None
    low, high = wilson_ci_95(6, 6)  # type: ignore[misc]
    assert 0.55 < low <= 1.0
    assert high == 1.0
    low0, high0 = wilson_ci_95(0, 8)  # type: ignore[misc]
    assert low0 == 0.0
    assert 0.25 < high0 < 0.45


def test_build_report_cards_flags_zero_yield_sink_and_recommends_higher_tier() -> None:
    runs = [
        # 6 clean first-pass claude runs opening PRs
        *[
            _make_run(
                f"r_claude_{i}",
                provider="claude",
                model="claude-opus-4-6",
                status="succeeded",
                pr_number=2200 + i,
                input_tokens=30_000,
                output_tokens=3_000,
                cost_usd=0.30,
            )
            for i in range(1, 7)
        ],
        # 5 failed antigravity flash runs burning tokens with 0 PRs (tier mismatch / zero yield sink)
        *[
            _make_run(
                f"r_agy_{i}",
                provider="antigravity",
                model="gemini-3.8-flash-high",
                status="failed",
                exit_code=1,
                failure_class="unverified_output",
                verification="unverified",
                input_tokens=50_000,
                output_tokens=1_000,
                cost_usd=0.05,
            )
            for i in range(1, 6)
        ],
        # 1 codex run that opened PR 2301 but needed a pr-remediator follow-up
        _make_run(
            "r_codex_1",
            provider="codex",
            model="gpt-5.4",
            status="succeeded",
            pr_number=2301,
            branch="staff/codex-2301",
            input_tokens=25_000,
            output_tokens=2_500,
        ),
        _make_run(
            "r_rem_1",
            role="pr-remediator",
            provider="claude",
            model="claude-opus-4-6",
            status="succeeded",
            pr_number=2301,
            branch="staff/codex-2301",
            target_kind="pr",
            target_ref="2301",
        ),
    ]
    facts = {(full_repo("Runner_Dashboard"), 2201): PrFact(state="merged", ci_first_pass=True, followup_fix=False)}

    report = build_report_cards(runs, facts, available_providers=["claude", "codex", "antigravity"])
    assert report["total_runs_analyzed"] == len(runs)
    board = {f"{c['provider']}/{c['model']}": c for c in report["leaderboard"]}

    claude_card = board["claude/claude-opus-4-6"]
    assert claude_card["rank"] == 1
    assert claude_card["first_pass_success_rate"] == 1.0
    assert claude_card["prs_merged"] >= 1
    assert claude_card["grade"] in ("A", "B")

    agy_card = board["antigravity/gemini-3.8-flash-high"]
    assert agy_card["succeeded_runs"] == 0
    assert agy_card["effective_tokens_per_success"] is None
    assert agy_card["infinite_cost_warning"] is True
    assert "zero_yield_token_sink" in agy_card["flags"]
    assert "tier_mismatch_waste" in agy_card["flags"]
    assert "unverified_output_churn" in agy_card["flags"]

    codex_card = board["codex/gpt-5.4"]
    assert codex_card["remediation_followup_runs"] == 1
    assert codex_card["first_pass_succeeded_runs"] == 0

    ledger = report["bean_counter_ledger"]
    assert ledger["wasted_tokens"] >= 5 * 51_000
    assert any(
        a["provider"] == "antigravity" and a["kind"] == "tier_reassignment" for a in ledger["economization_actions"]
    )

    recs = {(r["role"], r["complexity_tier"]): r for r in report["routing_recommendations"]}
    ir_rec = recs[("issue-remediator", "standard")]
    assert ir_rec["recommended_provider"] == "claude"
    assert "antigravity/gemini-3.8-flash-high" in ir_rec["avoid_models"]


def test_barb_continuity_parallel_leases_turnover_compliance_and_fenced_handoff(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "barb_test.db")

    # 1. Parallel scoped checkout across distinct scopes succeeds
    c1 = store.checkout_lease(
        scope="barb:front-door", holder="grok-1", persona="grok-barb", machine="DeskComputer", summary="Front door"
    )
    c2 = store.checkout_lease(
        scope="barb:ci-shepherd", holder="claude-1", persona="claude-barb", machine="Brick", summary="Watching CI"
    )
    assert c1["acquired"] is True
    assert c2["acquired"] is True

    # Competing checkout on same active scope without force is rejected
    conflict = store.checkout_lease(scope="barb:front-door", holder="codex-1", persona="codex-barb")
    assert conflict["acquired"] is False
    assert conflict["conflict"] is True

    # 2. Release without turnover log fails compliance check
    non_compliant = store.release_lease(scope="barb:front-door", holder="grok-1")
    assert non_compliant["released"] is False
    assert non_compliant["reason"] == "turnover_required_before_release"
    assert non_compliant["turnover_compliant"] is False

    # Logging turnover satisfies compliance and allows release
    to_entry = store.append_turnover(
        scope="barb:front-door",
        persona="grok-barb",
        author="grok-1",
        machine="DeskComputer",
        summary="Triaged 4 issues and routed 2 PRs.",
        completed=["RD #2278 merged"],
        next_actions=["Monitor Brick overnight runs"],
    )
    assert to_entry["id"].startswith("to-")

    released = store.release_lease(scope="barb:front-door", holder="grok-1")
    assert released["released"] is True
    assert released["turnover_compliant"] is True

    # 3. Task surface classification & fenced CLI <-> Desktop handoff
    cls_gui = classify_task_surface(
        "Re-authenticate OAuth session in browser on ControlTower", requires_code_edit=False
    )
    assert cls_gui["requires_desktop"] is True
    assert cls_gui["surface"] == "desktop_app"

    cls_cli = classify_task_surface("Fix ruff lint error in backend/staff/store.py")
    assert cls_cli["requires_desktop"] is False
    assert cls_cli["surface"] == "headless_cli"

    task = store.upsert_task(
        title="Fix ControlTower dashboard auth flow",
        scope="barb:dispatch-coordinator",
        repo="Runner_Dashboard",
        issue_or_pr="2269",
    )
    tid = task["task_id"]
    assert task["fencing_epoch"] == 1

    # CLI requests desktop handoff -> epoch 2
    h1 = store.transition_handoff(
        task_id=tid,
        target_state="needs_desktop",
        actor_persona="codex-barb",
        handoff_request="Needs interactive browser OAuth login",
        expected_epoch=1,
    )
    assert h1["transitioned"] is True
    assert h1["task"]["handoff_state"] == "needs_desktop"
    assert h1["task"]["status"] == "blocked_desktop"
    assert h1["task"]["fencing_epoch"] == 2

    # Stale epoch is rejected (split-brain prevention)
    stale = store.transition_handoff(
        task_id=tid,
        target_state="desktop_claimed",
        actor_persona="stale-worker",
        expected_epoch=1,
    )
    assert stale["transitioned"] is False
    assert stale["reason"] == "stale_fencing_epoch"

    # Desktop claims -> completes -> CLI resumes
    h2 = store.transition_handoff(
        task_id=tid, target_state="desktop_claimed", actor_persona="cursor-barb", expected_epoch=2
    )
    assert h2["transitioned"] is True and h2["task"]["handoff_state"] == "desktop_claimed"

    h3 = store.transition_handoff(
        task_id=tid,
        target_state="resume_ready",
        actor_persona="cursor-barb",
        artifact_ref="session://controltower/oauth-ok",
        handoff_resolution="OAuth cookie refreshed; CLI can resume pytest verification.",
        expected_epoch=3,
    )
    assert h3["transitioned"] is True
    assert h3["task"]["handoff_state"] == "resume_ready"
    assert h3["task"]["status"] == "in_progress"

    h4 = store.transition_handoff(task_id=tid, target_state="completed", actor_persona="codex-barb", expected_epoch=4)
    assert h4["transitioned"] is True
    assert h4["task"]["status"] == "completed"
    assert h4["task"]["artifact_ref"] == "session://controltower/oauth-ok"


def test_api_routes_for_report_cards_and_barb_continuity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staff_db = RunStore(tmp_path / "staff.db")
    r = _make_run("r_api_1", provider="claude", model="claude-opus-4-6", pr_number=2278)
    staff_db.create_run(r)
    staff_db.update_run(
        "r_api_1",
        status="succeeded",
        exit_code=0,
        input_tokens=15_000,
        output_tokens=1_500,
        cost_usd=0.18,
        verification="verified",
        pr_number=2278,
    )

    barb_store = BarbContinuityStore(tmp_path / "barb.db")
    fake_runner = SimpleNamespace(store=staff_db, machine="DeskComputer")
    monkeypatch.setattr(staff_outcomes, "get_runner", lambda: fake_runner)
    monkeypatch.setattr(staff_usage, "get_runner", lambda: fake_runner)
    monkeypatch.setattr(staff_barb, "get_runner", lambda: fake_runner)
    monkeypatch.setattr(staff_barb, "get_barb_continuity_store", lambda: barb_store)

    app = FastAPI()
    app.include_router(staff_outcomes.router, prefix="/api/v1/staff")
    app.include_router(staff_usage.router)
    app.include_router(staff_barb.router, prefix="/api/v1/staff")
    principal = Principal(id="test", type="human", name="test", roles=["admin"], scopes=["staff:read", "staff:write"])
    for mod in (staff_outcomes, staff_usage, staff_barb):
        for route in mod.router.routes:
            for dep in getattr(route, "dependant", SimpleNamespace(dependencies=[])).dependencies:
                app.dependency_overrides[dep.call] = lambda: principal

    client = TestClient(app)

    for path in (
        "/api/v1/staff/report-cards",
        "/api/v1/staff/leaderboard",
        "/api/staff/report-cards",
        "/api/staff/leaderboard",
    ):
        resp = client.get(path, params={"since": "2020-01-01T00:00:00Z"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["machine"] == "DeskComputer"
        assert data["total_runs_analyzed"] == 1
        assert data["leaderboard"][0]["provider"] == "claude"

    bc = client.get("/api/v1/staff/bean-counter", params={"since": "2020-01-01T00:00:00Z"})
    assert bc.status_code == 200
    assert "bean_counter_ledger" in bc.json()

    co = client.post(
        "/api/v1/staff/barb/leases/checkout",
        json={"scope": "barb:front-door", "holder": "grok-1", "persona": "grok-barb"},
    )
    assert co.status_code == 200
    assert co.json()["lease"]["scope"] == "barb:front-door"

    snap = client.get("/api/v1/staff/barb/continuity")
    assert snap.status_code == 200
    assert len(snap.json()["active_leases"]) == 1


def test_leaderboard_recommend_agent_and_barb_leases_route(tmp_path: Path, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import Principal
    from routers import staff_barb
    from staff.barb_continuity import BarbContinuityStore
    from staff.leaderboard import recommend_agent

    runs = [
        _make_run(
            "r1", provider="claude", model="default", role="implementer", repo="Runner_Dashboard", status="succeeded"
        ),
        _make_run(
            "r2", provider="claude", model="default", role="implementer", repo="Runner_Dashboard", status="succeeded"
        ),
        _make_run(
            "r3", provider="claude", model="default", role="implementer", repo="Runner_Dashboard", status="succeeded"
        ),
        _make_run(
            "r4",
            provider="antigravity",
            model="gemini-3.8-flash-high",
            role="implementer",
            repo="Runner_Dashboard",
            status="failed",
        ),
    ]
    rec = recommend_agent("implementer", "Runner_Dashboard", "medium", runs=runs)
    assert rec["recommended_provider"] == "claude"
    assert rec["repo"] == "Runner_Dashboard"
    assert rec["complexity_tier"] == "medium"

    fallback_rec = recommend_agent("tooling-scout", "Repository_Management", "high", runs=[])
    assert fallback_rec["recommended_provider"] == "claude"
    assert fallback_rec["confidence"] == "heuristic_fallback"

    from types import SimpleNamespace

    store = BarbContinuityStore(tmp_path / "barb_leases.db")
    monkeypatch.setattr(staff_barb, "get_barb_continuity_store", lambda: store)
    monkeypatch.setattr(staff_barb, "get_runner", lambda: SimpleNamespace(machine="DeskComputer"))
    app = FastAPI()
    app.include_router(staff_barb.router, prefix="/api/v1/staff")
    principal = Principal(id="test", type="human", name="Test", roles=["admin"], scopes=["staff:read", "staff:write"])
    for route in staff_barb.router.routes:
        for dep in getattr(route, "dependant", SimpleNamespace(dependencies=[])).dependencies:
            app.dependency_overrides[dep.call] = lambda: principal
    client = TestClient(app)
    post_res = client.post(
        "/api/v1/staff/barb/leases",
        json={"scope": "barb:front-door", "holder": "grok-barb", "persona": "grok-barb"},
    )
    assert post_res.status_code == 200
    get_res = client.get("/api/v1/staff/barb/leases")
    assert get_res.status_code == 200
    assert get_res.json()["count"] == 1
    assert get_res.json()["leases"][0]["scope"] == "barb:front-door"
