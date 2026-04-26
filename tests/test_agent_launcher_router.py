"""Tests for backend/agent_launcher_router.py.

Strategy: the router is a thin shell over subprocess + file-system reads.
We exercise the file-based paths (status, lock, state, pidfile) with real
temp dirs, and use monkeypatch for the subprocess paths so the suite never
actually shells out to a launcher CLI that may not exist on the test box.
"""

from __future__ import annotations  # noqa: E402

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import agent_launcher_router as alr  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def runtime(tmp_path: Path, monkeypatch) -> Path:
    """Point the router at a temp runtime root so tests don't touch the
    operator's real %LOCALAPPDATA%."""
    monkeypatch.setattr(alr, "_runtime_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def app(runtime):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(alr.router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# /status — pure file reads
# ---------------------------------------------------------------------------
def test_status_no_state_no_config_returns_empty_agents(runtime, client, monkeypatch):
    """Cold start: nothing on disk, no scheduler running, no agents
    configured. Should return a coherent empty response, not 500."""
    monkeypatch.setattr(alr, "_is_pid_alive", lambda pid: False)
    r = client.get("/api/agent-launcher/status")
    assert r.status_code == 200
    body = r.json()
    assert body["scheduler_running"] is False
    assert body["scheduler_pid"] is None
    assert body["agents"] == []
    assert body["runtime_root"] == str(runtime)


def test_status_with_running_scheduler_and_one_agent(runtime, client, monkeypatch):
    (runtime / "config.json").write_text(
        json.dumps(
            {"agents": {"address-issues": {"enabled": True, "interval_seconds": 3600}}}
        ),
        encoding="utf-8",
    )
    (runtime / "scheduler.pid").write_text(
        json.dumps({"pid": 99999, "started_iso": "2026-04-25T12:00:00Z"}),
        encoding="utf-8",
    )
    (runtime / "state.json").write_text(
        json.dumps(
            {
                "agents": {
                    "address-issues": {
                        "runs": [
                            {
                                "started_iso": "2026-04-25T13:00:00Z",
                                "repo": "Tools",
                                "window_pid": 4242,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(alr, "_is_pid_alive", lambda pid: pid == 99999)

    r = client.get("/api/agent-launcher/status")
    assert r.status_code == 200
    body = r.json()
    assert body["scheduler_running"] is True
    assert body["scheduler_pid"] == 99999
    assert len(body["agents"]) == 1
    a = body["agents"][0]
    assert a["name"] == "address-issues"
    assert a["last_repo"] == "Tools"
    assert a["last_window_pid"] == 4242
    assert a["lock_alive"] is False  # no lock file yet


def test_status_lock_alive_when_pid_alive(runtime, client, monkeypatch):
    (runtime / "config.json").write_text(
        json.dumps(
            {"agents": {"address-prs": {"enabled": True, "interval_seconds": 3600}}}
        ),
        encoding="utf-8",
    )
    (runtime / "locks").mkdir()
    (runtime / "locks" / "address-prs.lock").write_text(
        json.dumps(
            {
                "pid": 12345,
                "started_iso": "2026-04-25T13:00:00Z",
                "skill": "/address-prs",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(alr, "_is_pid_alive", lambda pid: pid == 12345)
    r = client.get("/api/agent-launcher/status")
    a = r.json()["agents"][0]
    assert a["lock_alive"] is True


def test_status_corrupt_state_does_not_500(runtime, client, monkeypatch):
    """Status must degrade gracefully — a half-written JSON shouldn't blank
    the dashboard."""
    (runtime / "state.json").write_text("{ this is not json", encoding="utf-8")
    (runtime / "config.json").write_text(
        json.dumps({"agents": {"a": {}}}), encoding="utf-8"
    )
    monkeypatch.setattr(alr, "_is_pid_alive", lambda pid: False)
    r = client.get("/api/agent-launcher/status")
    assert r.status_code == 200
    assert len(r.json()["agents"]) == 1


# ---------------------------------------------------------------------------
# /config GET / PUT — DbC at the boundary
# ---------------------------------------------------------------------------
def test_get_config_delegates_to_launcher(client, monkeypatch):
    fake_normalized = {"version": 2, "agents": {"x": {}}}
    monkeypatch.setattr(
        alr,
        "_run_cli",
        lambda *a, **kw: (0, json.dumps(fake_normalized), ""),
    )
    r = client.get("/api/agent-launcher/config")
    assert r.status_code == 200
    assert r.json() == fake_normalized


def test_get_config_503_when_launcher_missing(client, monkeypatch):
    monkeypatch.setattr(alr, "_launcher_root", lambda: None)
    r = client.get("/api/agent-launcher/config")
    assert r.status_code == 503
    assert "not found" in r.json()["detail"].lower()


def test_put_config_validates_before_promoting(runtime, client, monkeypatch):
    """A bad config must NOT overwrite the existing config.json."""
    (runtime / "config.json").write_text('{"version": 2}', encoding="utf-8")
    monkeypatch.setattr(
        alr, "_run_cli", lambda *a, **kw: (2, "", "model.provider must be one of [...]")
    )
    r = client.put("/api/agent-launcher/config", json={"bad": "config"})
    assert r.status_code == 422
    assert "rejected" in r.json()["detail"].lower()
    # Old config must still be there.
    assert (runtime / "config.json").read_text(encoding="utf-8") == '{"version": 2}'
    # The .next temp file must have been cleaned up.
    assert not (runtime / "config.json.next").exists()


def test_put_config_atomic_rename_on_success(runtime, client, monkeypatch):
    monkeypatch.setattr(alr, "_run_cli", lambda *a, **kw: (0, "{}", ""))
    new_cfg = {"version": 2, "agents": {"x": {}}}
    r = client.put("/api/agent-launcher/config", json=new_cfg)
    assert r.status_code == 200, r.json()
    written = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    assert written == new_cfg


def test_put_config_rejects_non_json(client):
    r = client.put(
        "/api/agent-launcher/config",
        content="not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_put_config_rejects_non_object(runtime, client):
    r = client.put("/api/agent-launcher/config", json=[1, 2, 3])
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /repos
# ---------------------------------------------------------------------------
def test_get_repos_passes_through_launcher_json(client, monkeypatch):
    fake = {
        "wsl_distro": "Ubuntu-22.04",
        "repos_root": "~/Linux_Repositories",
        "org_filter": "D-sorganization",
        "count": 2,
        "repos": [
            {
                "name": "Tools",
                "wsl_path": "/x/Tools",
                "org": "D-sorganization",
                "remote_url": "git@github.com:D-sorganization/Tools.git",
            },
            {
                "name": "Games",
                "wsl_path": "/x/Games",
                "org": "D-sorganization",
                "remote_url": "git@github.com:D-sorganization/Games.git",
            },
        ],
    }
    monkeypatch.setattr(alr, "_run_cli", lambda *a, **kw: (0, json.dumps(fake), ""))
    r = client.get("/api/agent-launcher/repos")
    assert r.status_code == 200
    assert r.json()["count"] == 2
    assert {r["name"] for r in r.json()["repos"]} == {"Tools", "Games"}


def test_get_repos_502_on_launcher_failure(client, monkeypatch):
    monkeypatch.setattr(alr, "_run_cli", lambda *a, **kw: (1, "", "wsl.exe missing"))
    r = client.get("/api/agent-launcher/repos")
    assert r.status_code == 502


# ---------------------------------------------------------------------------
# /run-once
# ---------------------------------------------------------------------------
def test_run_once_unknown_agent_returns_404(client, monkeypatch):
    # GET /config returns the validated config; we mock it to have only "x".
    monkeypatch.setattr(
        alr,
        "_run_cli",
        lambda *a, **kw: (0, json.dumps({"agents": {"x": {}}}), ""),
    )
    r = client.post("/api/agent-launcher/run-once", json={"agent": "nonexistent"})
    assert r.status_code == 404
    assert "nonexistent" in r.json()["detail"]


def test_run_once_known_agent_invokes_launcher(client, monkeypatch):
    calls = []

    def fake_cli(*args, **kw):
        calls.append(args)
        # First call is --validate-config (from get_config); subsequent is --once.
        if "--once" in args:
            return (0, "spawned", "")
        return (0, json.dumps({"agents": {"address-issues": {}}}), "")

    monkeypatch.setattr(alr, "_run_cli", fake_cli)
    r = client.post("/api/agent-launcher/run-once", json={"agent": "address-issues"})
    assert r.status_code == 200
    assert any("--once" in c for c in calls)


def test_run_once_validates_request_body(client):
    r = client.post("/api/agent-launcher/run-once", json={})
    assert r.status_code == 422  # pydantic validation failure


# ---------------------------------------------------------------------------
# /stop
# ---------------------------------------------------------------------------
def test_stop_returns_ok_false_when_not_running(client, monkeypatch):
    monkeypatch.setattr(alr, "_run_cli", lambda *a, **kw: (4, "", ""))
    r = client.post("/api/agent-launcher/stop")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_stop_returns_ok_true_on_success(client, monkeypatch):
    monkeypatch.setattr(alr, "_run_cli", lambda *a, **kw: (0, "stop requested", ""))
    r = client.post("/api/agent-launcher/stop")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
def test_start_no_op_if_already_running(runtime, client, monkeypatch):
    (runtime / "scheduler.pid").write_text(
        json.dumps({"pid": 99999, "started_iso": "2026-04-25T12:00:00Z"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(alr, "_is_pid_alive", lambda pid: True)
    r = client.post("/api/agent-launcher/start")
    assert r.status_code == 200
    assert "already running" in r.json()["detail"]
