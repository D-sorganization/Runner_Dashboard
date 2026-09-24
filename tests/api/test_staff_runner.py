"""Staff Hub core (epic #1192, issue #1194): roles, adapters, store, runner, routes.

Strategy: no real provider CLI is ever launched. A ``fake`` adapter points at the
current Python interpreter running a tiny script that prints stream-json lines,
so the full queued → running → succeeded path, event streaming, usage capture
and cancellation are exercised end-to-end without network or model access.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from staff import adapters as adapters_mod
from staff import roles as roles_mod
from staff import runner as runner_mod
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}

_FAKE_CLI = """
import json, sys, time
prompt = sys.argv[1]
print(json.dumps({"type": "system", "message": "booting"}))
content = [{"type": "text", "text": "working on: " + prompt[:30]}]
print(json.dumps({"type": "assistant", "message": {"content": content}}))
if "--slow" in sys.argv:
    time.sleep(30)
if "--fail" in sys.argv:
    sys.exit(3)
if "--ask" in sys.argv:
    print(json.dumps({"type": "result", "result": "Should I proceed with git commit?"}))
    sys.exit(0)
usage = {"input_tokens": 120, "output_tokens": 30}
print(json.dumps({"type": "result", "result": "STAFF_RESULT: done", "usage": usage, "total_cost_usd": 0.0123}))
"""


@pytest.fixture
def fake_cli(tmp_path: Path) -> Path:
    script = tmp_path / "fake_cli.py"
    script.write_text(_FAKE_CLI, encoding="utf-8")
    return script


@pytest.fixture
def role_dir(tmp_path: Path) -> Path:
    d = tmp_path / "roles"
    d.mkdir()
    (d / "night-watch.yml").write_text(
        "\n".join(
            [
                "name: night-watch",
                "title: Night Watch",
                "summary: Triage simple issues overnight.",
                "playbook: docs/fleet-night-watch.md",
                "prompt_template: null",
                "instructions: Triage simple issues overnight.",
                "providers: [fake, claude]",
                "model: default",
                "schedule: '0 22 * * *'",
                "window: {start: '22:00', end: '06:00'}",
                "repos: [UpstreamDrift, Tools]",
                "scope: {}",
                "budget: {usd_per_run: 3, usd_per_day: 15}",
                (
                    "permissions: {lease: false, push_branch: true, open_pr: true, "
                    "merge: false, host_shell: false, notify_user: false}"
                ),
                "reports_to: orchestrator",
                "holds: ['no bulk stale-queue cancel']",
                "surface: dashboard",
            ]
        ),
        encoding="utf-8",
    )
    (d / "barb.yml").write_text(
        "\n".join(
            [
                "name: barb",
                "title: Barb",
                "summary: Personal secretary.",
                "playbook: docs/fleet-barb.md",
                "prompt_template: null",
                "instructions: Secretary tasks.",
                "providers: [grok-chat]",
                "model: default",
                "schedule: null",
                "window: null",
                "repos: [UpstreamDrift]",
                "scope: {}",
                "budget: {usd_per_run: 0.5, usd_per_day: 2}",
                (
                    "permissions: {lease: false, push_branch: false, open_pr: false, "
                    "merge: false, host_shell: false, notify_user: true}"
                ),
                "reports_to: user",
                "holds: []",
                "surface: grok-chat",
            ]
        ),
        encoding="utf-8",
    )
    (d / "broken.yml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    return d


@pytest.fixture
def staff(
    tmp_path: Path, fake_cli: Path, role_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[runner_mod.StaffRunner]:
    """A runner wired to a temp store, temp roles and the fake provider."""
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(role_dir))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path / "nonexistent-repos"))
    fake = adapters_mod.ProviderAdapter(
        provider_id="fake",
        label="Fake CLI",
        executable=sys.executable,
        argv=(str(fake_cli), "{prompt}", "--model", "{model}"),
        json_lines=True,
    )
    slow = adapters_mod.ProviderAdapter(
        provider_id="fake-slow",
        label="Slow",
        executable=sys.executable,
        argv=(str(fake_cli), "{prompt}", "--slow"),
        json_lines=True,
    )
    failing = adapters_mod.ProviderAdapter(
        provider_id="fake-fail",
        label="Fail",
        executable=sys.executable,
        argv=(str(fake_cli), "{prompt}", "--fail"),
        json_lines=True,
    )
    asking = adapters_mod.ProviderAdapter(
        provider_id="fake-ask",
        label="Ask",
        executable=sys.executable,
        argv=(str(fake_cli), "{prompt}", "--ask"),
        json_lines=True,
    )
    adapters = {
        **adapters_mod.ADAPTERS,
        "fake": fake,
        "fake-slow": slow,
        "fake-fail": failing,
        "fake-ask": asking,
    }
    monkeypatch.setattr(adapters_mod, "ADAPTERS", adapters)
    store_mod.reset_store()
    runner_mod.reset_runner()
    r = runner_mod.StaffRunner(
        store=store_mod.RunStore(tmp_path / "runs.sqlite3"),
        adapters=adapters,
        machine="TestNode",
    )
    monkeypatch.setattr(runner_mod, "_runner", r)
    yield r
    store_mod.reset_store()
    runner_mod.reset_runner()


def _wait(
    store: store_mod.RunStore,
    run_id: str,
    until: tuple[str, ...],
    timeout: float = 20.0,
) -> store_mod.RunRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = store.get_run(run_id)
        assert rec is not None
        if rec.status in until:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {until}; last status {store.get_run(run_id).status}")  # type: ignore[union-attr]


# ── roles ────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_load_roles_reads_yaml_skips_broken_and_adds_builtin(role_dir: Path) -> None:
    roles = roles_mod.load_roles(role_dir)
    assert set(roles) == {"night-watch", "barb", "broken", "ad-hoc"}
    nw = roles["night-watch"]
    assert nw.providers == ("fake", "claude")
    assert nw.window == {"start": "22:00", "end": "06:00"}
    assert nw.budget_usd_per_day == 15.0
    assert nw.dispatchable
    assert nw.valid
    assert not roles["barb"].dispatchable  # grok-chat surface is never a CLI run
    assert roles["barb"].valid
    assert not roles["broken"].dispatchable  # invalid roles surfaced as not dispatchable (#1300)
    assert not roles["broken"].valid
    assert roles["broken"].errors


@pytest.mark.unit
def test_roles_dir_env_override_wins(role_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_ROLES_DIR", str(role_dir))
    assert roles_mod.roles_dir() == role_dir
    monkeypatch.setenv("STAFF_ROLES_DIR", str(role_dir / "missing"))
    assert roles_mod.roles_dir() is None


# ── adapters ─────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_build_command_substitutes_and_drops_empty_model_flag() -> None:
    claude = adapters_mod.ADAPTERS["claude"]
    argv = claude.build_command("do the thing", "/tmp/wt", model=None)
    assert argv[0] == "claude"
    assert argv[-2:] == [
        "--model",
        "sonnet",
    ]  # role model "default" never falls through to the CLI's own default
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "do the thing" in argv
    codex = adapters_mod.ADAPTERS["codex"]
    assert "--model" not in codex.build_command("x", "/tmp/wt", model=None)
    argv2 = claude.build_command("x", "/tmp/wt", model="claude-opus-5")
    assert argv2[-2:] == ["--model", "claude-opus-5"]


@pytest.mark.unit
def test_build_command_rejects_empty_prompt() -> None:
    with pytest.raises(AssertionError):
        adapters_mod.ADAPTERS["codex"].build_command("   ", "/tmp")


@pytest.mark.unit
def test_parse_line_json_text_and_usage() -> None:
    claude = adapters_mod.ADAPTERS["claude"]
    ev = claude.parse_line(
        json.dumps(
            {
                "type": "result",
                "result": "ok",
                "usage": {"input_tokens": 5, "output_tokens": 7},
                "total_cost_usd": 0.5,
            }
        )
    )
    assert ev["kind"] == "result" and ev["text"] == "ok"
    assert ev["usage"] == {"input_tokens": 5, "output_tokens": 7, "cost_usd": 0.5}
    assert claude.parse_line("not json") == {"kind": "text", "text": "not json"}
    assert claude.parse_line("{broken") == {"kind": "text", "text": "{broken"}
    codex = adapters_mod.ADAPTERS["codex"]
    assert codex.parse_line('{"type": "x"}')["kind"] == "text"  # plain-text provider never parses


@pytest.mark.unit
def test_every_builtin_adapter_has_a_prompt_slot_or_stdin() -> None:
    for pid, adapter in adapters_mod.ADAPTERS.items():
        assert adapter.prompt_via_stdin or any("{prompt}" in a for a in adapter.argv), pid


# ── store ────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_store_round_trip_events_and_spend(tmp_path: Path) -> None:
    store = store_mod.RunStore(tmp_path / "s.sqlite3")
    rec = store_mod.RunRecord(
        id="run-1",
        role="r",
        provider="p",
        model=None,
        machine="m",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="x",
    )
    store.create_run(rec)
    assert store.get_run("run-1").status == "queued"  # type: ignore[union-attr]
    store.update_run("run-1", status="succeeded", cost_usd=1.25)
    assert store.append_event("run-1", "text", "hello") == 1
    assert store.append_event("run-1", "text", "world") == 2
    assert [e["text"] for e in store.events_after("run-1", 1)] == ["world"]
    assert store.get_run("run-1").last_line == "world"  # type: ignore[union-attr]
    assert store.spend_since("2000-01-01T00:00:00Z") == {"p": 1.25, "total": 1.25}
    assert store.list_runs(status="succeeded")[0].id == "run-1"
    with pytest.raises(AssertionError):
        store.update_run("run-1", status="bogus")
    with pytest.raises(AssertionError):
        store.update_run("run-1", nonexistent=1)
    store.close()


# ── runner ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_plan_validates_role_provider_and_target(staff: runner_mod.StaffRunner) -> None:
    with pytest.raises(ValueError, match="unknown role"):
        staff.plan(runner_mod.RunRequest(role="nope", prompt="x"))
    with pytest.raises(ValueError, match="not dispatchable"):
        staff.plan(runner_mod.RunRequest(role="barb", prompt="x"))
    with pytest.raises(ValueError, match="not allowed for role"):
        staff.plan(runner_mod.RunRequest(role="night-watch", provider="codex", prompt="x"))
    with pytest.raises(ValueError, match="scoped to"):
        staff.plan(runner_mod.RunRequest(role="night-watch", provider="fake", repo="AffineDrift", issue=1))
    with pytest.raises(ValueError, match="one of issue, pr or prompt"):
        staff.plan(runner_mod.RunRequest(role="night-watch", provider="fake"))
    plan = staff.plan(runner_mod.RunRequest(role="night-watch", provider="fake", prompt="sweep"))
    assert plan.provider == "fake" and plan.argv[0] == sys.executable
    assert "Night Watch" in plan.prompt and "DRAFT pull request" in plan.prompt
    assert plan.branch.startswith("staff/night-watch-task-")
    assert plan.lease_ritual is False


@pytest.mark.integration
def test_submit_runs_fake_cli_to_success_with_events_and_cost(
    staff: runner_mod.StaffRunner,
) -> None:
    rec = staff.submit(
        runner_mod.RunRequest(
            role="night-watch",
            provider="fake",
            prompt="sweep the backlog",
            requested_by="tester",
        )
    )
    assert rec.status == "queued"
    done = _wait(staff.store, rec.id, ("succeeded", "failed"))
    assert done.status == "succeeded", staff.store.events_after(rec.id)
    assert done.exit_code == 0
    assert done.cost_usd == pytest.approx(0.0123)
    assert done.input_tokens == 120 and done.output_tokens == 30
    kinds = [e["kind"] for e in staff.store.events_after(rec.id)]
    assert kinds[:2] == ["queued", "start"]
    assert "result" in kinds and kinds[-1] == "exit"
    assert Path(done.transcript_path).is_file()
    assert Path(done.workdir).is_dir()


@pytest.mark.integration
def test_failed_exit_code_marks_run_failed(staff: runner_mod.StaffRunner) -> None:
    rec = staff.submit(runner_mod.RunRequest(role="ad-hoc", provider="fake-fail", prompt="boom"))
    done = _wait(staff.store, rec.id, ("succeeded", "failed"))
    assert done.status == "failed" and done.exit_code == 3


@pytest.mark.integration
def test_cancel_terminates_running_process(staff: runner_mod.StaffRunner) -> None:
    rec = staff.submit(runner_mod.RunRequest(role="ad-hoc", provider="fake-slow", prompt="sleep"))
    _wait(staff.store, rec.id, ("running",))
    assert staff.cancel(rec.id) is True
    done = _wait(staff.store, rec.id, ("cancelled", "failed", "succeeded"))
    assert done.status == "cancelled"
    assert staff.cancel(rec.id) is False  # nothing left to cancel


# ── routes ───────────────────────────────────────────────────────────────
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
def test_roster_lists_roles_and_providers(client: TestClient) -> None:
    body = client.get("/api/staff/roster").json()
    assert body["machine"] == "TestNode"
    names = {r["name"] for r in body["roles"]}
    assert {"night-watch", "barb", "ad-hoc"} <= names
    assert "claude" in body["providers"]
    assert body["active_runs"] == 0


@pytest.mark.unit
def test_dry_run_returns_plan_without_creating_a_run(client: TestClient, staff: runner_mod.StaffRunner) -> None:
    resp = client.post(
        "/api/staff/night-watch/run",
        json={"provider": "fake", "prompt": "preview", "dry_run": True},
        headers=_XHR,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["plan"]["provider"] == "fake"
    assert "preview" in body["plan"]["prompt"]
    assert staff.store.list_runs() == []


@pytest.mark.unit
def test_dispatch_rejects_bad_role_provider_and_repo(client: TestClient) -> None:
    assert client.post("/api/staff/nope/run", json={"prompt": "x"}, headers=_XHR).status_code == 422
    assert (
        client.post(
            "/api/staff/night-watch/run",
            json={"provider": "codex", "prompt": "x"},
            headers=_XHR,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/staff/night-watch/run",
            json={"provider": "fake", "repo": "org/repo", "prompt": "x"},
            headers=_XHR,
        ).status_code
        == 422
    )
    assert client.post("/api/staff/night-watch/run", json={"provider": "fake"}, headers=_XHR).status_code == 422


@pytest.mark.integration
def test_dispatch_then_get_list_board_and_stream(client: TestClient, staff: runner_mod.StaffRunner) -> None:
    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={"provider": "fake", "prompt": "hello api"},
        headers=_XHR,
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run"]["id"]
    _wait(staff.store, run_id, ("succeeded", "failed"))

    one = client.get(f"/api/staff/runs/{run_id}").json()
    assert one["run"]["status"] == "succeeded"
    assert any(e["kind"] == "result" for e in one["events"])

    listed = client.get("/api/staff/runs", params={"role": "ad-hoc"}).json()
    assert listed["count"] == 1 and listed["runs"][0]["id"] == run_id
    assert client.get("/api/staff/runs", params={"status": "bogus"}).status_code == 422

    board = client.get("/api/staff/board").json()
    assert board["machine"] == "TestNode"
    assert board["running"] == [] and board["queued"] == []
    assert board["recent"][0]["id"] == run_id
    assert board["spend_today_usd"]["total"] == pytest.approx(0.0123)

    with client.stream("GET", f"/api/staff/runs/{run_id}/stream") as s:
        text = "".join(s.iter_text())
    assert "event: queued" in text
    assert "event: result" in text
    assert text.rstrip().endswith("}") and "event: end" in text

    assert client.get("/api/staff/runs/run-missing").status_code == 404
    assert client.get("/api/staff/runs/run-missing/stream").status_code == 404


@pytest.mark.integration
def test_cancel_route(client: TestClient, staff: runner_mod.StaffRunner) -> None:
    run_id = client.post(
        "/api/staff/ad-hoc/run",
        json={"provider": "fake-slow", "prompt": "zzz"},
        headers=_XHR,
    ).json()["run"]["id"]
    _wait(staff.store, run_id, ("running",))
    resp = client.post(f"/api/staff/runs/{run_id}/cancel", headers=_XHR)
    assert resp.status_code == 200 and resp.json()["cancelled"] is True
    assert _wait(staff.store, run_id, ("cancelled",)).status == "cancelled"
    assert client.post("/api/staff/runs/nope/cancel", headers=_XHR).status_code == 404


@pytest.mark.integration
def test_exit_zero_without_staff_result_is_failed(
    staff: runner_mod.StaffRunner,
) -> None:
    rec = staff.submit(runner_mod.RunRequest(role="ad-hoc", provider="fake-ask", prompt="sweep"))
    done = _wait(staff.store, rec.id, ("succeeded", "failed"))
    assert done.status == "failed" and done.exit_code == 0
    assert done.error == runner_mod.NO_RESULT_ERROR
