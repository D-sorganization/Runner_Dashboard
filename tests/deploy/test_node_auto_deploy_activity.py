from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.activity import (  # noqa: E402
    ActivityUnavailableError,
    fetch_staff_activity,
    local_activity_count,
    staff_database_path,
)


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE runs (id TEXT, status TEXT);CREATE TABLE messages (id TEXT, author_kind TEXT, delivery TEXT);"
    )
    return connection


def test_activity_count_includes_runs_and_pending_chat_replies(tmp_path: Path) -> None:
    database = tmp_path / "staff.sqlite3"
    connection = _database(database)
    connection.executemany("INSERT INTO runs VALUES (?, ?)", [("r1", "running"), ("r2", "queued"), ("r3", "succeeded")])
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?)",
        [
            ("m1", "role", "streaming"),
            ("m2", "system", "pending"),
            ("m3", "user", "pending"),
            ("m4", "role", "complete"),
        ],
    )
    connection.commit()
    connection.close()
    assert local_activity_count(database) == 4


def test_missing_or_unknown_activity_store_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ActivityUnavailableError, match="could not read"):
        local_activity_count(tmp_path / "missing.sqlite3")
    database = tmp_path / "unknown.sqlite3"
    sqlite3.connect(database).close()
    with pytest.raises(ActivityUnavailableError, match="schema"):
        local_activity_count(database)


def test_database_path_honors_runtime_overrides(tmp_path: Path) -> None:
    assert staff_database_path({"STAFF_RUNS_DB": str(tmp_path / "custom.db")}) == tmp_path / "custom.db"
    assert staff_database_path({"RUNNER_DASHBOARD_CONFIG_DIR": str(tmp_path)}) == tmp_path / "staff_runs.sqlite3"


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        import json

        self._body = json.dumps(payload).encode()
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def getcode(self) -> int:
        return self.status


class _Opener:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.request = None

    def open(self, request: object, timeout: float) -> _Response:
        self.request = request
        assert timeout <= 3.0
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_fetch_staff_activity_uses_summary_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import node_auto_deploy.activity as activity

    database = tmp_path / "staff.sqlite3"
    _database(database).close()
    opener = _Opener(_Response({"active": 2, "queued": 1, "pending_chat": 0, "generated_at": "2026-10-27T00:00:00Z"}))
    monkeypatch.setattr(activity, "build_opener", lambda *_handlers: opener)

    assert fetch_staff_activity("http://127.0.0.1:8321", "node-token", database) == 2
    assert opener.request.full_url == "http://127.0.0.1:8321/api/staff/activity/summary"
    assert "runs?limit=" not in opener.request.full_url
    assert opener.request.get_header("Authorization") == "Bearer node-token"


def test_fetch_staff_activity_takes_max_with_local_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import node_auto_deploy.activity as activity

    database = tmp_path / "staff.sqlite3"
    connection = _database(database)
    connection.execute("INSERT INTO runs VALUES ('busy', 'running')")
    connection.commit()
    connection.close()
    opener = _Opener(_Response({"active": 0, "queued": 0, "pending_chat": 0, "generated_at": "x"}))
    monkeypatch.setattr(activity, "build_opener", lambda *_handlers: opener)

    assert fetch_staff_activity("http://127.0.0.1:8321", "node-token", database) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"active": "two", "queued": 0, "pending_chat": 0},
        {"active": -1, "queued": 0, "pending_chat": 0},
        {"active": True, "queued": 0, "pending_chat": 0},
        {"queued": 0, "pending_chat": 0},
        "not-a-dict",
    ],
)
def test_fetch_staff_activity_fails_closed_on_404_auth_or_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    import node_auto_deploy.activity as activity

    database = tmp_path / "staff.sqlite3"
    _database(database).close()
    monkeypatch.setattr(activity, "build_opener", lambda *_handlers: _Opener(_Response(payload)))

    with pytest.raises(ActivityUnavailableError, match="activity summary"):
        fetch_staff_activity("http://127.0.0.1:8321", "node-token", database)


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_staff_activity_rejects_http_auth_and_service_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    import node_auto_deploy.activity as activity

    database = tmp_path / "staff.sqlite3"
    _database(database).close()
    monkeypatch.setattr(activity, "build_opener", lambda *_handlers: _Opener(_Response({}, status=status)))

    with pytest.raises(ActivityUnavailableError, match="activity summary"):
        fetch_staff_activity("http://127.0.0.1:8321", "node-token", database)


def test_staff_activity_transport_failure_does_not_fall_back_to_database_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from urllib.error import URLError

    import node_auto_deploy.activity as activity

    database = tmp_path / "staff.sqlite3"
    _database(database).close()
    monkeypatch.setattr(activity, "build_opener", lambda *_handlers: _Opener(URLError("offline")))

    with pytest.raises(ActivityUnavailableError, match="activity summary"):
        fetch_staff_activity("http://127.0.0.1:8321", "node-token", database)


def test_staff_activity_requires_token_and_loopback_base_url(tmp_path: Path) -> None:
    database = tmp_path / "staff.sqlite3"
    _database(database).close()
    with pytest.raises(ActivityUnavailableError):
        fetch_staff_activity("http://127.0.0.1:8321", None, database)
    with pytest.raises(ActivityUnavailableError):
        fetch_staff_activity("https://example.invalid", "node-token", database)


def test_staff_activity_rejects_oversized_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import node_auto_deploy.activity as activity

    database = tmp_path / "staff.sqlite3"
    _database(database).close()
    oversized = _Response({"runs": [], "count": 0, "padding": "x" * (1024 * 1024)})
    monkeypatch.setattr(activity, "build_opener", lambda *_handlers: _Opener(oversized))

    with pytest.raises(ActivityUnavailableError, match="size limit"):
        fetch_staff_activity("http://127.0.0.1:8321", "node-token", database)


def test_node_dry_run_uses_local_probe_without_fetching_fleet_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import node_auto_deploy.artifact as artifact
    from node_auto_deploy.state import AutoDeployState

    entrypoint = REPO / "deploy" / "node-auto-deploy.py"
    spec = importlib.util.spec_from_file_location("node_auto_deploy_entrypoint_under_test", entrypoint)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    live_dir = tmp_path / "dashboard"
    (live_dir / "backend").mkdir(parents=True)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(module, "auto_deploy_disabled", lambda: False)
    monkeypatch.setattr(module, "load_token_from_env_file", lambda _path: "local-staff-token")
    monkeypatch.setattr(module, "default_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(module, "load_state", lambda _path: AutoDeployState())
    monkeypatch.setattr(module, "_resolve_role", lambda _backend: "node")
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setattr(module, "github_get", lambda _path, _token: [])
    monkeypatch.setattr(
        module,
        "fetch_staff_activity",
        lambda base, token, database: calls.append(("local", (base, token, database))) or 0,
    )

    fleet_calls: list[str] = []

    def evaluate(_config: object, deps: object) -> tuple[str, str, str]:
        assert deps.staff_in_flight() == 0
        return "ready", "4.10.2", "a" * 40

    monkeypatch.setattr(module, "evaluate_cycle", evaluate)
    monkeypatch.setattr(module, "fetch_fleet_status", lambda *_args: fleet_calls.append("hub") or {})
    monkeypatch.setattr(artifact, "pick_release_assets", lambda *_args: {"version": "4.10.2"})

    assert module.main(["--deploy-dir", str(live_dir), "--env-file", str(tmp_path / "env"), "--dry-run"]) == 0
    assert fleet_calls == []
    expected_call = ("local", ("http://127.0.0.1:8321", "local-staff-token", module.staff_database_path()))
    assert calls == [expected_call, expected_call]
