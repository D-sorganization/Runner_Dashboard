"""Staff run GitHub App installation token env (#2038)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from staff.github_run_env import (
    GITHUB_INSTALLATION_TOKEN_MAX_RUN_SECONDS,
    StaffGithubToken,
    build_staff_run_env,
    cap_run_timeout_for_github_token,
    github_permissions_for_role,
    mint_staff_run_github_token,
    operator_token_fallback_enabled,
    strip_github_secrets_from_child_env,
)
from staff.roles import RoleSpec
from staff.runner import RunRecord, RunRequest, StaffRunner
from staff.store import RunStore

_FAKE_GHS = "ghs_" + "A" * 40 + "_jwtbody"
_FAKE_GHP = "ghp_" + "Z" * 36


def _role(name: str = "maintenance", providers: tuple[str, ...] = ("dummy",), **perms: Any) -> RoleSpec:
    base = {
        "lease": False,
        "push_branch": False,
        "open_pr": False,
        "merge": False,
        "host_shell": False,
        "notify_user": False,
    }
    base.update(perms)
    return RoleSpec(
        name=name,
        title=name.title(),
        providers=providers,
        permissions=base,
    )


def test_github_permissions_read_only_role() -> None:
    role = _role(code_read_only=True, push_branch=True)
    perms = github_permissions_for_role(role)
    assert perms == {
        "contents": "read",
        "pull_requests": "read",
        "issues": "read",
        "metadata": "read",
        "checks": "read",
    }


def test_github_permissions_write_when_push_or_pr() -> None:
    role = _role(push_branch=True, open_pr=True)
    perms = github_permissions_for_role(role)
    assert perms["contents"] == "write"
    assert perms["pull_requests"] == "write"
    assert perms["issues"] == "write"
    assert perms["checks"] == "read"
    assert perms["workflows"] == "read"


def test_strip_operator_tokens_and_app_private_key() -> None:
    parent = {
        "PATH": "/usr/bin",
        "GH_TOKEN": _FAKE_GHP,
        "GITHUB_TOKEN": _FAKE_GHP,
        "GITHUB_APP_PRIVATE_KEY_FILE": "/secret/key.pem",
        "GITHUB_APP_PRIVATE_KEY": "inline-key-placeholder-not-pem",  # pragma: allowlist secret
        "GITHUB_APP_ID": "123",
        "HOME": "/home/runner",
    }
    child = strip_github_secrets_from_child_env(parent)
    assert child["PATH"] == "/usr/bin"
    assert "GH_TOKEN" not in child
    assert "GITHUB_TOKEN" not in child
    assert "GITHUB_APP_PRIVATE_KEY_FILE" not in child
    assert "GITHUB_APP_PRIVATE_KEY" not in child
    assert "GITHUB_APP_ID" not in child


def test_build_staff_run_env_injects_token_and_git_config() -> None:
    base = {"PATH": "/bin", "GH_TOKEN": _FAKE_GHP}
    env = build_staff_run_env(base_env=base, github_token=_FAKE_GHS)
    assert env["GH_TOKEN"] == _FAKE_GHS
    assert env["GITHUB_TOKEN"] == _FAKE_GHS
    assert "GH_TOKEN" not in base or base["GH_TOKEN"] == _FAKE_GHP
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert "x-access-token" in env["GIT_CONFIG_VALUE_0"]
    assert _FAKE_GHS in env["GIT_CONFIG_VALUE_0"]
    assert _FAKE_GHP not in env["GIT_CONFIG_VALUE_0"]


def test_mint_staff_run_github_token_fail_closed_without_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_ALLOW_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with patch("staff.github_run_env.github_app_auth_configured", return_value=False):
        with pytest.raises(RuntimeError, match="GitHub App"):
            mint_staff_run_github_token(role=_role(), repo="D-sorganization/Runner_Dashboard")


def test_mint_staff_run_github_token_operator_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_ALLOW_OPERATOR_TOKEN", "1")
    monkeypatch.setenv("GH_TOKEN", _FAKE_GHP)
    with patch("staff.github_run_env.github_app_auth_configured", return_value=False):
        tok = mint_staff_run_github_token(role=_role(), repo="")
    assert tok.token == _FAKE_GHP
    assert tok.source == "operator"


def test_mint_staff_run_github_token_uses_app_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_ALLOW_OPERATOR_TOKEN", raising=False)
    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=1_700_000_000.0, source="github_app")
    with patch("staff.github_run_env.github_app_auth_configured", return_value=True):
        with patch("staff.github_run_env.mint_installation_access_token", return_value=fake) as mint:
            tok = mint_staff_run_github_token(
                role=_role(push_branch=True),
                repo="D-sorganization/Runner_Dashboard",
            )
    assert tok.token == _FAKE_GHS
    mint.assert_called_once()
    kwargs = mint.call_args.kwargs
    assert kwargs["repositories"] == ["D-sorganization/Runner_Dashboard"]
    assert kwargs["permissions"]["contents"] == "write"


def test_cap_run_timeout_for_github_token() -> None:
    assert cap_run_timeout_for_github_token(7200.0) == float(GITHUB_INSTALLATION_TOKEN_MAX_RUN_SECONDS)
    assert cap_run_timeout_for_github_token(600.0) == 600.0


def test_runner_auth_error_when_github_mint_fails(tmp_path: Any) -> None:
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

    runner._adapters = {"dummy": DummyAdapter()}
    runner._roles_loader = lambda: {"maintenance": _role()}

    rec = RunRecord(
        id="run-gh-auth-fail",
        role="maintenance",
        provider="dummy",
        model="default",
        machine="local",
        repo="D-sorganization/Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="test",
    )
    store.create_run(rec)
    plan = runner.plan(RunRequest(role="maintenance", prompt="test", provider="dummy"))
    workdir = tmp_path / "work"
    workdir.mkdir()

    with patch("staff.runner.prepare_staff_run_github_env", side_effect=RuntimeError("no app")):
        with patch("subprocess.Popen") as mock_popen:
            runner._execute(rec, plan, workdir, lease_note="")
            assert not mock_popen.called

    updated = store.get_run(rec.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.failure_class == "auth_error"
    assert "GITHUB_APP" in (updated.remediation or "")


def test_runner_injects_scoped_github_token(tmp_path: Any) -> None:
    store = RunStore(tmp_path / "runs.db")
    runner = StaffRunner(store=store)
    captured_env: dict[str, str] = {}

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

    runner._adapters = {"dummy": DummyAdapter()}
    runner._roles_loader = lambda: {"maintenance": _role(push_branch=True)}

    rec = RunRecord(
        id="run-gh-inject",
        role="maintenance",
        provider="dummy",
        model="default",
        machine="local",
        repo="D-sorganization/Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="test",
    )
    store.create_run(rec)
    plan = runner.plan(RunRequest(role="maintenance", prompt="test", provider="dummy"))
    workdir = tmp_path / "work"
    workdir.mkdir()

    fake = StaffGithubToken(token=_FAKE_GHS, expires_at=1_700_000_000.0, source="github_app")

    def capture_call(*args: Any, **kwargs: Any) -> MagicMock:
        captured_env.update(kwargs.get("env", {}))
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = iter(["STAFF_RESULT: ok\n"])
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        return mock_proc

    env_patch, _cred = build_staff_run_env({"PATH": "/bin", "GH_TOKEN": _FAKE_GHP}, _FAKE_GHS), StaffGithubToken(
        token=_FAKE_GHS, expires_at=0.0, source="github_app"
    )

    with patch.dict(os.environ, {"GH_TOKEN": _FAKE_GHP}, clear=False):
        with patch("staff.runner.prepare_staff_run_github_env", return_value=(env_patch, _cred)):
            with patch("subprocess.Popen", side_effect=capture_call):
                runner._execute(rec, plan, workdir, lease_note="")

    assert captured_env["GH_TOKEN"] == _FAKE_GHS
    assert captured_env["GITHUB_TOKEN"] == _FAKE_GHS
    assert _FAKE_GHP not in captured_env.values()

    updated = store.get_run(rec.id)
    blob = (updated.error or "") + (updated.remediation or "")
    assert _FAKE_GHS not in blob
    assert _FAKE_GHP not in blob


def test_redaction_masks_ghs_installation_token() -> None:
    from staff.redaction import redact_sensitive_content

    line = f"Authorization: {_FAKE_GHS} done"
    assert _FAKE_GHS not in redact_sensitive_content(line)
    assert "[REDACTED_GITHUB_TOKEN]" in redact_sensitive_content(line)


def test_e2e_hermetic_returns_fake_installation_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_E2E_HERMETIC", "1")
    tok = mint_staff_run_github_token(role=_role(), repo="")
    assert tok.source == "e2e_hermetic"
    assert tok.token.startswith("ghs_")


def test_operator_token_fallback_enabled() -> None:
    assert not operator_token_fallback_enabled()
    with patch.dict(os.environ, {"STAFF_ALLOW_OPERATOR_TOKEN": "1"}):
        assert operator_token_fallback_enabled()
