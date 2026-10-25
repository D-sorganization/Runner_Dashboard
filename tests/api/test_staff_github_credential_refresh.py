"""Staff run GitHub credential refresh via fleet API (#2038)."""

from __future__ import annotations

import io
import os
import sys
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from server import app
from staff.github_credential_client import fetch_github_token_for_run
from staff.github_git_credential import run_git_credential_helper
from staff.github_run_env import StaffGithubToken, build_staff_run_env
from staff.runner import RunRecord, RunRequest, StaffRunner
from staff.store import RunStore
from staff.tokens import mint_run_token

_FAKE_GHS = "ghs_" + "R" * 40 + "_refresh"
_XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_build_staff_run_env_uses_credential_helper_not_static_git_token() -> None:
    env = build_staff_run_env(
        {"PATH": "/usr/bin"},
        _FAKE_GHS,
        run_id="run-refresh-01",
        dashboard_port="8321",
        python_executable=sys.executable,
        real_gh_path="/usr/bin/gh",
    )
    assert env["GH_TOKEN"] == _FAKE_GHS
    assert "x-access-token" not in env.get("GIT_CONFIG_VALUE_0", "")
    assert env.get("GIT_CONFIG_KEY_0") == "credential.https://github.com.helper"
    assert env["STAFF_GITHUB_CREDENTIAL_URL"].endswith("/api/staff/runs/run-refresh-01/github-credential")
    assert env["STAFF_REAL_GH"] == "/usr/bin/gh"
    assert env["STAFF_REAL_GH_PATH"] == "/usr/bin/gh"
    wrapper_path = env["PATH"].split(os.pathsep)[0].replace("\\", "/").rstrip("/")
    assert wrapper_path.endswith("/staff/wrappers")


def test_runner_does_not_cap_four_hour_wall_clock(tmp_path: Any) -> None:
    store = RunStore(tmp_path / "runs.db")
    runner = StaffRunner(store=store)

    class DummyAdapter:
        executable = "dummy"
        prompt_via_stdin = False
        lease_agent = "dummy"

        def installed(self) -> bool:
            return True

        def build_command(self, prompt: str, workdir: str, model: str | None = None, **kwargs: Any) -> list[str]:
            return ["dummy"]

        def runtime_env(self) -> dict[str, str]:
            return {}

        def parse_line(self, line: str) -> dict[str, Any]:
            return {"kind": "text", "text": line}

    from staff.roles import RoleSpec

    role = RoleSpec(
        name="maintenance",
        title="M",
        providers=("dummy",),
        permissions={},
        budget_max_minutes=240.0,
    )
    runner._adapters = {"dummy": DummyAdapter()}
    runner._roles_loader = lambda: {"maintenance": role}

    rec = RunRecord(
        id="run-long",
        role="maintenance",
        provider="dummy",
        model="default",
        machine="local",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="x",
    )
    store.create_run(rec)
    plan = runner.plan(RunRequest(role="maintenance", prompt="x", provider="dummy"))
    workdir = tmp_path / "w"
    workdir.mkdir()

    seen_max: list[float] = []

    class _Watchdog:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen_max.append(float(kwargs.get("max_seconds", 0)))

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def record_output(self) -> None:
            return None

        failure_class = ""
        error_message = ""

    cred = StaffGithubToken(token=_FAKE_GHS, expires_at=0.0, source="test")
    env_patch = build_staff_run_env(
        {"PATH": "/bin"},
        _FAKE_GHS,
        run_id=rec.id,
        dashboard_port="8321",
        python_executable=sys.executable,
        real_gh_path="/usr/bin/gh",
    )

    with patch.dict(os.environ, {"STAFF_RUN_TIMEOUT_SECONDS": "14400"}, clear=False):
        with patch("staff.runner_ops.prepare_staff_run_github_env", return_value=(env_patch, cred)):
            with patch("staff.runner.StaffWatchdog", _Watchdog):
                with patch("subprocess.Popen") as mock_popen:
                    mock_proc = mock_popen.return_value
                    mock_proc.pid = 1
                    mock_proc.stdout = iter(["STAFF_RESULT: ok\n"])
                    mock_proc.wait.return_value = 0
                    mock_proc.poll.return_value = 0
                    runner._execute(rec, plan, workdir, lease_note="")

    assert seen_max, "watchdog should have been constructed"
    assert seen_max[0] == 14400.0


def test_github_credential_endpoint_requires_matching_run_principal(client: TestClient) -> None:
    run_id = "run-cred-auth"
    fleet = mint_run_token(role="maintenance", run_id=run_id, fleet_actions=[], ttl_seconds=600.0)
    headers = {"Authorization": f"Bearer {fleet}", **_XHR}
    wrong = client.post("/api/staff/runs/other-run/github-credential", headers=headers)
    assert wrong.status_code == 403


def test_github_credential_endpoint_returns_fresh_token(
    client: TestClient, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-cred-ok"
    from staff.runner import StaffRunner, reset_runner

    reset_runner()
    store = RunStore(tmp_path / "runs.sqlite3")  # type: ignore[operator]
    runner = StaffRunner(store=store)
    monkeypatch.setattr("staff.runner._runner", runner)
    monkeypatch.setattr("staff.github_credential_service.get_runner", lambda: runner)
    rec = RunRecord(
        id=run_id,
        role="maintenance",
        provider="dummy",
        model="default",
        machine="local",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="x",
        status="running",
    )
    store.create_run(rec)

    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=9_999_999_999.0, source="github_app")
    monkeypatch.setattr(
        "routers.staff.mint_github_credential_for_run",
        lambda _rid: fake,
    )

    fleet = mint_run_token(role="maintenance", run_id=run_id, fleet_actions=[], ttl_seconds=600.0)
    resp = client.post(
        f"/api/staff/runs/{run_id}/github-credential",
        headers={"Authorization": f"Bearer {fleet}", **_XHR},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == _FAKE_GHS
    assert body["expires_at"] > 0


def test_git_credential_helper_fails_closed_when_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_GITHUB_CREDENTIAL_URL", "http://127.0.0.1:8321/api/staff/runs/r1/github-credential")
    monkeypatch.setenv("FLEET_API_TOKEN", "run_testtoken")
    stdin = io.StringIO("protocol=https\nhost=github.com\n\n")
    with patch(
        "staff.github_git_credential.fetch_github_token_for_run",
        side_effect=RuntimeError("mint failed"),
    ):
        with patch("staff.github_git_credential.sys.stdin", stdin), patch(
            "staff.github_git_credential.sys.argv", ["git-credential-fleet-gh", "get"]
        ):
            code = __import__("staff.github_git_credential", fromlist=["main"]).main()
    assert code == 1
    assert stdin.read() == ""  # consumed


def test_git_credential_helper_fetches_via_fleet_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_GITHUB_CREDENTIAL_URL", "http://127.0.0.1:8321/api/staff/runs/r1/github-credential")
    monkeypatch.setenv("FLEET_API_TOKEN", "run_testtoken")

    with patch(
        "staff.github_git_credential.fetch_github_token_for_run",
        return_value=_FAKE_GHS,
    ) as fetch:
        stdin = io.StringIO("protocol=https\nhost=github.com\n\n")
        out = io.StringIO()
        run_git_credential_helper(stdin=stdin, stdout=out)
        fetch.assert_called_once()
    text = out.getvalue()
    assert "username=x-access-token" in text
    assert _FAKE_GHS in text


def test_fetch_github_token_for_run_uses_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_GITHUB_CREDENTIAL_URL", "http://127.0.0.1:9/cred")
    monkeypatch.setenv("FLEET_API_TOKEN", "run_secrettoken")  # pragma: allowlist secret

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"token": _FAKE_GHS, "expires_at": 123.0}

    with patch("httpx.post", return_value=_Resp()) as post:
        tok = fetch_github_token_for_run()
    assert tok == _FAKE_GHS
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer run_secrettoken"
