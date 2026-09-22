"""Staff Hub usage ledger (epic #1192, issue #1200): pricing, store aggregation,
cost finalisation, ``/api/staff/usage*`` routes and the RM export subprocess.

No provider CLI and no Repository_Management checkout is needed: the export
test monkeypatches ``subprocess.run`` and points ``rm_root`` at a temp dir.
"""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff import pricing, usage
from staff import runner as runner_mod
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _rec(run_id: str, **overrides: Any) -> store_mod.RunRecord:
    base: dict[str, Any] = {
        "id": run_id,
        "role": "night-watch",
        "provider": "claude",
        "model": "claude-opus-5",
        "machine": "TestNode",
        "repo": "",
        "target_kind": "prompt",
        "target_ref": "",
        "prompt": "x",
        "status": "succeeded",
        "created_at": "2026-09-22T10:00:00Z",
        "started_at": "2026-09-22T10:00:00Z",
        "ended_at": "2026-09-22T10:02:00Z",
    }
    base.update(overrides)
    return store_mod.RunRecord(**base)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[store_mod.RunStore]:
    s = store_mod.RunStore(tmp_path / "runs.sqlite3")
    yield s
    s.close()


# ── pricing ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_token_table_math_for_claude_models() -> None:
    usd, method = pricing.estimate_cost("claude", "claude-opus-5", input_tokens=1_000_000, output_tokens=100_000)
    assert method == "token_table" and usd == pytest.approx(5.0 + 2.5)
    usd, _ = pricing.estimate_cost("claude", "claude-fable-5-1-20260701", 2_000, 1_000)
    assert usd == pytest.approx(0.02 + 0.05)
    usd, _ = pricing.estimate_cost("claude", "sonnet-5", 1_000_000, 0)
    assert usd == pytest.approx(2.0)
    usd, _ = pricing.estimate_cost("claude", "claude-haiku-4-5", 0, 1_000_000)
    assert usd == pytest.approx(5.0)
    assert pricing.lookup_price("claude", "claude-sonnet-4-6") == pricing.Price(3.0, 15.0)
    assert pricing.lookup_price("claude", None) == pricing.PRICE_TABLE["claude"]["opus-5"]  # default model


@pytest.mark.unit
def test_ported_rows_are_marked_estimates_and_ollama_is_free() -> None:
    assert pricing.lookup_price("codex", "gpt-5").estimate is True  # type: ignore[union-attr]
    assert pricing.lookup_price("gemini", "gemini-2.5-pro").estimate is True  # type: ignore[union-attr]
    assert all(not p.estimate for p in pricing.PRICE_TABLE["claude"].values())
    assert pricing.lookup_price("ollama", "llama3.1") == pricing.Price(0.0, 0.0)
    assert pricing.estimate_cost("ollama", "llama3.1", 5_000, 5_000, wall_seconds=600) == (0.0, "token_table")
    assert pricing.lookup_price("claude", "totally-unknown") is None
    assert pricing.estimate_cost("claude", "totally-unknown", 10, 10) == (0.0, "none")


@pytest.mark.unit
def test_wall_time_fallback_defaults_to_zero_and_honours_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_WALL_USD_PER_MIN", raising=False)
    assert pricing.estimate_cost("cursor-agent", "grok", wall_seconds=300) == (0.0, "none")
    monkeypatch.setenv("STAFF_WALL_USD_PER_MIN", "cursor-agent=0.10,antigravity=abc,ollama=-5")
    usd, method = pricing.estimate_cost("cursor-agent", "grok", wall_seconds=300)
    assert method == "wall_time" and usd == pytest.approx(0.5)
    assert pricing.estimate_cost("antigravity", None, wall_seconds=300) == (0.0, "none")
    assert pricing.estimate_cost("ollama", None, wall_seconds=300) == (0.0, "none")
    assert pricing.estimate_cost("cursor-agent", "grok", input_tokens=-5, wall_seconds=-1) == (0.0, "none")
    rows = pricing.price_table_rows()
    assert any(r["provider"] == "cursor-agent" and r.get("wall_usd_per_minute") == 0.10 for r in rows)


# ── store ─────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_cost_method_column_is_added_to_an_existing_store(tmp_path: Path) -> None:
    """Two-step reversible migration: an old DB without ``cost_method`` upgrades in place."""
    path = tmp_path / "old.sqlite3"
    legacy_schema = store_mod._SCHEMA  # the shipped CREATE TABLE has no cost_method column
    assert "cost_method" not in legacy_schema
    conn = sqlite3.connect(str(path))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO runs (id, role, provider, machine, target_kind, prompt, status, created_at, cost_usd) "
        "VALUES ('run-old', 'r', 'claude', 'm', 'prompt', 'p', 'succeeded', '2026-09-22T00:00:00Z', 0.5)"
    )
    conn.commit()
    conn.close()
    s = store_mod.RunStore(path)
    assert "cost_method" in s.columns()
    old = s.get_run("run-old")
    assert old is not None and old.cost_method == "" and old.cost_usd == 0.5
    s.update_run("run-old", cost_method="reported")
    assert s.get_run("run-old").cost_method == "reported"  # type: ignore[union-attr]
    s.close()
    assert "cost_method" in store_mod.RunStore(path).columns()  # idempotent on reopen


@pytest.mark.unit
def test_usage_by_provider_role_and_day(store: store_mod.RunStore) -> None:
    store.create_run(_rec("a", cost_usd=1.0, input_tokens=100, output_tokens=10))
    store.create_run(_rec("b", provider="codex", role="steward", cost_usd=0.25, input_tokens=50, output_tokens=5))
    store.create_run(
        _rec("c", created_at="2026-09-21T23:00:00Z", started_at="2026-09-21T23:00:00Z", ended_at=None, cost_usd=0.5)
    )
    by_provider = {r["key"]: r for r in store.usage_by("provider")}
    assert by_provider["claude"]["runs"] == 2 and by_provider["claude"]["cost_usd"] == pytest.approx(1.5)
    assert by_provider["claude"]["input_tokens"] == 100 and by_provider["codex"]["output_tokens"] == 5
    assert by_provider["claude"]["wall_seconds"] == pytest.approx(120.0)  # run c has no ended_at
    assert by_provider["codex"]["wall_seconds"] == pytest.approx(120.0)
    by_role = {r["key"]: r["runs"] for r in store.usage_by("role")}
    assert by_role == {"night-watch": 2, "steward": 1}
    by_day = [r["key"] for r in store.usage_by("day")]
    assert by_day == ["2026-09-21", "2026-09-22"]
    since = store.usage_by("day", since="2026-09-22T00:00:00Z")
    assert [r["key"] for r in since] == ["2026-09-22"] and since[0]["runs"] == 2
    assert store.usage_by("provider", since="2099-01-01T00:00:00Z") == []
    with pytest.raises(AssertionError):
        store.usage_by("machine")


# ── finalize_cost ─────────────────────────────────────────────────────────
@pytest.mark.unit
def test_finalize_cost_keeps_reported_and_estimates_otherwise(
    store: store_mod.RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_WALL_USD_PER_MIN", "cursor-agent=0.50")
    store.create_run(_rec("reported", cost_usd=0.0123, input_tokens=120, output_tokens=30))
    assert usage.finalize_cost(store, "reported", "claude", "claude-opus-5") == "reported"
    rec = store.get_run("reported")
    assert rec is not None and rec.cost_usd == pytest.approx(0.0123) and rec.cost_method == "reported"

    store.create_run(_rec("tokens", cost_usd=0.0, input_tokens=1_000_000, output_tokens=0))
    assert usage.finalize_cost(store, "tokens", "claude", "claude-sonnet-5") == "token_table"
    assert store.get_run("tokens").cost_usd == pytest.approx(2.0)  # type: ignore[union-attr]

    store.create_run(_rec("wall", provider="cursor-agent", model="grok", cost_usd=0.0))
    assert usage.finalize_cost(store, "wall", "cursor-agent", "grok") == "wall_time"
    wall = store.get_run("wall")
    assert wall is not None and wall.cost_usd == pytest.approx(1.0) and wall.cost_method == "wall_time"

    store.create_run(_rec("nothing", provider="codex", model="gpt-5", cost_usd=0.0, ended_at=None))
    assert usage.finalize_cost(store, "nothing", "codex", "gpt-5") == "none"
    assert store.get_run("nothing").cost_method == "none"  # type: ignore[union-attr]
    assert usage.finalize_cost(store, "missing", "claude", None) == "none"


@pytest.mark.unit
def test_summary_totals_and_budget(store: store_mod.RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    today = usage.today_iso()
    store.create_run(_rec("t1", created_at=today, cost_usd=2.5, input_tokens=10, output_tokens=5))
    store.create_run(_rec("t2", created_at=today, provider="codex", cost_usd=0.5))
    monkeypatch.setenv("STAFF_BUDGET_USD_PER_DAY", "12")
    body = usage.summary(store, group="provider", since=today)
    assert body["totals"]["cost_usd"] == pytest.approx(3.0) and body["totals"]["runs"] == 2
    assert body["totals"]["input_tokens"] == 10 and body["totals"]["wall_seconds"] == pytest.approx(240.0)
    assert body["budget"] == {"usd_per_day": 12.0, "spent_today_usd": pytest.approx(3.0), "percent_used": 25.0}
    monkeypatch.setenv("STAFF_BUDGET_USD_PER_DAY", "not-a-number")
    assert usage.summary(store)["budget"]["usd_per_day"] == 0.0
    monkeypatch.delenv("STAFF_BUDGET_USD_PER_DAY")
    assert usage.summary(store)["budget"]["percent_used"] is None


# ── export subprocess ─────────────────────────────────────────────────────
@pytest.mark.unit
def test_export_to_rm_invokes_script_per_provider(store: store_mod.RunStore, tmp_path: Path) -> None:
    rm = tmp_path / "Repository_Management"
    (rm / "scripts").mkdir(parents=True)
    (rm / "scripts" / "append_credit_usage.py").write_text("# stub\n", encoding="utf-8")
    store.create_run(_rec("e1", created_at="2026-09-22T01:00:00Z", cost_usd=0.4, input_tokens=900, output_tokens=100))
    store.create_run(_rec("e2", created_at="2026-09-22T02:00:00Z", provider="codex", model="gpt-5", cost_usd=0.1))
    store.create_run(_rec("old", created_at="2026-09-20T02:00:00Z", cost_usd=9.0))
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["cwd"] == str(rm)
        rc = 1 if "codex" in argv else 0
        return subprocess.CompletedProcess(argv, rc, stdout="added: {}", stderr="")

    result = usage.export_to_rm(store, rm, date="2026-09-22", run=fake_run)
    assert result["date"] == "2026-09-22" and result["ok"] is False
    assert [c["provider"] for c in result["exported"]] == ["claude", "codex"]
    claude_argv = calls[0]
    assert claude_argv[1].endswith("append_credit_usage.py")
    assert claude_argv[claude_argv.index("--cost-category") + 1] == "claude_code"
    assert claude_argv[claude_argv.index("--estimated-tokens") + 1] == "1000"
    assert claude_argv[claude_argv.index("--duration-minutes") + 1] == "2"
    assert claude_argv[claude_argv.index("--cost-usd") + 1] == "0.4"
    assert claude_argv[claude_argv.index("--source") + 1] == usage.EXPORT_SOURCE and "--replace" in claude_argv
    assert calls[1][calls[1].index("--cost-category") + 1] == "codex"
    assert result["exported"][1]["exit_code"] == 1
    with pytest.raises(FileNotFoundError):
        usage.export_to_rm(store, tmp_path / "nowhere", date="2026-09-22", run=fake_run)


# ── routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, store_mod.RunStore]]:
    from identity import require_fleet_peer, require_orchestrator_peer  # noqa: PLC0415
    from routers import staff_usage as usage_router  # noqa: PLC0415

    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    store_mod.reset_store()
    runner_mod.reset_runner()
    s = store_mod.RunStore(tmp_path / "runs.sqlite3")
    r = runner_mod.StaffRunner(store=s, adapters={}, machine="TestNode")
    monkeypatch.setattr(runner_mod, "_runner", r)
    app = FastAPI()
    app.include_router(usage_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_orchestrator_peer] = lambda: "test-orchestrator"
    with TestClient(app) as c:
        yield c, s
    app.dependency_overrides.clear()
    s.close()
    store_mod.reset_store()
    runner_mod.reset_runner()


@pytest.mark.unit
def test_usage_route_groups_and_validates(
    client: tuple[TestClient, store_mod.RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, s = client
    today = usage.today_iso()
    s.create_run(_rec("r1", created_at=today, cost_usd=1.0, input_tokens=10, output_tokens=1))
    s.create_run(_rec("r2", created_at="2026-01-01T00:00:00Z", role="steward", cost_usd=4.0))
    monkeypatch.setenv("STAFF_BUDGET_USD_PER_DAY", "10")
    body = c.get("/api/staff/usage").json()
    assert body["machine"] == "TestNode" and body["group"] == "provider"
    assert body["since"] == today and body["totals"]["runs"] == 1
    assert body["budget"]["percent_used"] == 10.0
    by_role = c.get("/api/staff/usage", params={"group": "role", "since": "2026-01-01T00:00:00Z"}).json()
    assert {r["key"] for r in by_role["rows"]} == {"night-watch", "steward"}
    assert by_role["totals"]["cost_usd"] == pytest.approx(5.0)
    assert c.get("/api/staff/usage", params={"group": "machine"}).status_code == 422
    pricing_rows = c.get("/api/staff/usage/pricing").json()["rows"]
    assert any(r["provider"] == "claude" and r["model"] == "opus-5" for r in pricing_rows)


@pytest.mark.unit
def test_export_route_503_without_rm_and_runs_export_with_it(
    client: tuple[TestClient, store_mod.RunStore], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, s = client
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "missing"))
    resp = c.post("/api/staff/usage/export", headers=_XHR)
    assert resp.status_code == 503 and "append_credit_usage.py" in resp.json()["detail"]

    rm = tmp_path / "Repository_Management"
    (rm / "scripts").mkdir(parents=True)
    (rm / "scripts" / "check_agent_claim.py").write_text("# stub\n", encoding="utf-8")
    (rm / "scripts" / "append_credit_usage.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm))
    s.create_run(_rec("x", created_at=usage.today_iso(), cost_usd=0.2))
    seen: list[list[str]] = []
    monkeypatch.setattr(
        usage.subprocess,
        "run",
        lambda argv, **kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="added: {}", stderr=""),
    )
    resp = c.post("/api/staff/usage/export", headers=_XHR)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["exported"][0]["provider"] == "claude"
    assert len(seen) == 1 and seen[0][1].endswith("append_credit_usage.py")
