"""Unit tests for credentials router refactoring (Issue #1151)."""

from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from cache_utils import cache_clear, cache_get, cache_set  # noqa: E402
from dashboard_config import CacheTtl  # noqa: E402
from routers import credentials  # noqa: E402
from routers.credentials import (  # noqa: E402
    LaunchAuthRequest,
    _build_credentials_summary,
    _collect_all_probes,
    _env_present_anywhere,
    _probe_anthropic_api,
    _probe_claude_code_cli,
    _probe_cline,
    _probe_codex_cli,
    _probe_gemini_cli,
    _probe_github_cli,
    _probe_jules_api,
    _probe_jules_cli,
    _probe_linear_workspaces,
    _probe_ollama,
    _resolve_cline_detail,
    _vscode_extension_installed,
    _vscode_has_extension,
    get_cline_status,
    get_credentials,
    get_ollama_models,
    get_ollama_status,
    launch_auth,
)


@pytest.fixture(autouse=True)
def _clear_cache_between_tests() -> Iterator[None]:
    cache_clear()
    yield
    cache_clear()


def _mock_request() -> Request:
    req = MagicMock(spec=Request)
    req.client.host = "127.0.0.1"
    req.headers.get = lambda key, default=None: None
    return req


@pytest.mark.asyncio
async def test_probe_github_cli_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: None if cmd == "gh" else "/bin/" + cmd)
    probe = await _probe_github_cli()
    assert probe["id"] == "github_cli"
    assert probe["installed"] is False
    assert probe["authenticated"] is False
    assert probe["usable"] is False
    assert probe["status"] == "not_installed"
    assert probe["detail"] == "gh not found"


@pytest.mark.asyncio
async def test_probe_github_cli_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    def mock_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="Logged in", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    probe = await _probe_github_cli()
    assert probe["installed"] is True
    assert probe["authenticated"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"
    assert probe["detail"] == "authenticated"


@pytest.mark.asyncio
async def test_probe_github_cli_not_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    def mock_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="Not logged in")

    monkeypatch.setattr(subprocess, "run", mock_run)
    probe = await _probe_github_cli()
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["usable"] is False
    assert probe["status"] == "not_authed"
    assert probe["detail"] == "not logged in"


@pytest.mark.asyncio
async def test_probe_github_cli_subprocess_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    def mock_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("Subprocess failure")

    monkeypatch.setattr(subprocess, "run", mock_run)
    with caplog.at_level(logging.WARNING):
        probe = await _probe_github_cli()
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["detail"] == "probe failed"
    assert any("GitHub CLI probe failed: OSError" in rec.message for rec in caplog.records)


def test_probe_jules_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/jules" if cmd == "jules" else None)
    probe = _probe_jules_cli()
    assert probe["id"] == "jules_cli"
    assert probe["installed"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"

    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: None)
    probe_missing = _probe_jules_cli()
    assert probe_missing["installed"] is False
    assert probe_missing["usable"] is False
    assert probe_missing["status"] == "not_installed"


def test_probe_jules_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    probe_missing = _probe_jules_api()
    assert probe_missing["id"] == "jules_api"
    assert probe_missing["authenticated"] is False
    assert probe_missing["usable"] is False
    assert probe_missing["status"] == "missing_key"

    monkeypatch.setenv("JULES_API_KEY", "jules-secret")
    probe_ok = _probe_jules_api()
    assert probe_ok["authenticated"] is True
    assert probe_ok["usable"] is True
    assert probe_ok["status"] == "ready"


def test_probe_anthropic_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ships inert (#3123 slice 4): no key exists yet, probe reports missing_key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY_ENV_VAR", raising=False)
    probe_missing = _probe_anthropic_api()
    assert probe_missing["id"] == "anthropic_api"
    assert probe_missing["authenticated"] is False
    assert probe_missing["usable"] is False
    assert probe_missing["status"] == "missing_key"
    assert "ANTHROPIC_API_KEY" in probe_missing["detail"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")  # pragma: allowlist secret
    probe_ok = _probe_anthropic_api()
    assert probe_ok["authenticated"] is True
    assert probe_ok["usable"] is True
    assert probe_ok["status"] == "ready"
    assert "sk-ant-test-value" not in str(probe_ok)  # the key's value never appears in any field


def test_probe_codex_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials, "_find_binary", lambda name: "/usr/bin/codex" if name == "codex" else None)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-login"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    probe = _probe_codex_cli()
    assert probe["id"] == "codex_cli"
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["usable"] is False
    assert probe["status"] == "missing_key"
    assert probe["detail"] == "not signed in: run `codex login`"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    probe_no_key = _probe_codex_cli()
    assert probe_no_key["installed"] is True
    assert probe_no_key["authenticated"] is False
    assert probe_no_key["usable"] is False
    assert probe_no_key["status"] == "missing_key"
    assert probe_no_key["detail"] == "not signed in: run `codex login`"

    monkeypatch.setattr(credentials, "_find_binary", lambda name: None)
    probe_not_installed = _probe_codex_cli()
    assert probe_not_installed["installed"] is False
    assert probe_not_installed["usable"] is False
    assert probe_not_installed["status"] == "not_installed"


def test_probe_codex_cli_login_file_counts_as_authenticated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials, "_find_binary", lambda name: "/usr/bin/codex" if name == "codex" else None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    probe = _probe_codex_cli()
    assert probe["authenticated"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"
    assert probe["detail"] == "Ready (codex login)"
    assert probe["key_status"] == "oauth"


def test_probe_claude_code_cli_api_key_env_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    monkeypatch.setattr(credentials, "_env_present_anywhere", lambda key: key == "ANTHROPIC_API_KEY")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-login"))
    monkeypatch.setattr(
        "staff.cli_oauth_session.claude_oauth_session_alive",
        lambda *_a, **_k: (False, "not signed in"),
    )
    probe = _probe_claude_code_cli()
    assert probe["id"] == "claude_code_cli"
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["usable"] is False
    assert probe["status"] == "missing_key"


def test_probe_claude_code_cli_expired_oauth_not_authenticated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    monkeypatch.setattr(credentials, "_env_present_anywhere", lambda key: False)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    monkeypatch.setattr(
        "staff.cli_oauth_session.claude_oauth_session_alive",
        lambda *_a, **_k: (False, "Authentication expired"),
    )
    probe = _probe_claude_code_cli()
    assert probe["authenticated"] is False
    assert "expired" in probe["detail"].lower()


def test_probe_claude_code_cli_no_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    monkeypatch.setattr(credentials, "_env_present_anywhere", lambda key: False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-login"))
    probe = _probe_claude_code_cli()
    assert probe["authenticated"] is False
    assert probe["usable"] is False
    assert probe["status"] == "missing_key"
    assert probe["detail"] == "not signed in: run `claude login`"


def test_probe_claude_code_cli_login_file_counts_as_authenticated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    monkeypatch.setattr(credentials, "_env_present_anywhere", lambda key: False)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    monkeypatch.setattr(
        "staff.cli_oauth_session.claude_oauth_session_alive",
        lambda *_a, **_k: (True, "OAuth session active"),
    )

    probe = _probe_claude_code_cli()
    assert probe["authenticated"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"
    assert probe["detail"] == "Ready (claude login)"
    assert probe["key_status"] == "oauth"


def test_probe_cline_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: None)
    probe = _probe_cline()
    assert probe["id"] == "cline"
    assert probe["label"] == "Cline CLI"
    assert probe["installed"] is False
    assert probe["usable"] is False
    assert probe["status"] == "not_installed"
    assert probe["detail"] == "cline not found on PATH"


def test_probe_cline_binary_without_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/cline" if cmd == "cline" else None)
    monkeypatch.setattr(credentials.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CLINE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    probe = _probe_cline()
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["usable"] is False
    assert probe["status"] == "missing_key"
    assert "cline auth" in probe["detail"]


def test_probe_cline_ready_with_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/cline" if cmd == "cline" else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-anthropic-api-key-fixture")

    probe = _probe_cline()
    assert probe["installed"] is True
    assert probe["authenticated"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"
    assert probe["detail"] == "Ready (cline auth)"


def test_probe_cline_ready_with_providers_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/cline" if cmd == "cline" else None)
    monkeypatch.setattr(credentials.Path, "home", lambda: tmp_path)
    providers = tmp_path / ".cline" / "data" / "settings" / "providers.json"
    providers.parent.mkdir(parents=True, exist_ok=True)
    cred_field = "api" + "Key"
    providers.write_text(
        json.dumps({"anthropic": {cred_field: "not-a-real-api-key-fixture"}}),
        encoding="utf-8",
    )

    probe = _probe_cline()
    assert probe["authenticated"] is True
    assert probe["status"] == "ready"


def test_probe_gemini_cli_ignores_api_key_without_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/gemini" if cmd == "gemini" else None)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setattr(credentials, "_gemini_oauth_present", lambda: False)
    probe = _probe_gemini_cli()
    assert probe["id"] == "gemini_cli"
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["usable"] is False
    assert probe["status"] == "missing_key"


def test_probe_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/ollama" if cmd == "ollama" else None)
    probe = _probe_ollama()
    assert probe["id"] == "ollama"
    assert probe["label"] == "Ollama"
    assert probe["installed"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"


@pytest.mark.asyncio
async def test_probe_linear_workspaces(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_summaries() -> list[dict[str, Any]]:
        return [
            {
                "id": "personal",
                "auth_status": "ok",
                "auth_kind": "api_key",
                "teams_filter": ["ENG", "PROD"],
                "default_repository": "D-sorganization/Runner_Dashboard",
            },
            {
                "id": "work",
                "auth_status": "missing_env",
                "auth_kind": "api_key",
                "teams_filter": ["*"],
            },
        ]

    monkeypatch.setattr(credentials, "list_workspace_summaries", mock_summaries)
    probes = await _probe_linear_workspaces()
    assert len(probes) == 2
    assert probes[0]["id"] == "linear:personal"
    assert probes[0]["usable"] is True
    assert probes[0]["status"] == "ready"
    assert probes[1]["id"] == "linear:work"
    assert probes[1]["usable"] is False
    assert probes[1]["status"] == "missing_env"


@pytest.mark.asyncio
async def test_probe_linear_workspaces_exception_handling(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def mock_summaries() -> list[dict[str, Any]]:
        raise ValueError("Linear API error")

    monkeypatch.setattr(credentials, "list_workspace_summaries", mock_summaries)
    with caplog.at_level(logging.WARNING):
        probes = await _probe_linear_workspaces()
    assert probes == []
    assert any(
        "Failed to enumerate Linear workspace credential probes: ValueError" in rec.message for rec in caplog.records
    )


def test_resolve_cline_detail() -> None:
    assert _resolve_cline_detail("/usr/bin/cline", True) == "Ready (cline auth)"
    assert _resolve_cline_detail("/usr/bin/cline", False) == "not signed in: run `cline auth`"
    assert _resolve_cline_detail(None, False) == "cline not found on PATH"


def test_build_credentials_summary() -> None:
    sample_probes = [
        {"id": "p1", "usable": True},
        {"id": "p2", "usable": False},
        {"id": "p3", "usable": True},
    ]
    summary = _build_credentials_summary(sample_probes)
    assert summary == {"total": 3, "ready": 2, "not_ready": 1}


@pytest.mark.asyncio
async def test_collect_all_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(credentials, "_find_binary", lambda cmd: None)
    monkeypatch.setattr(credentials, "_resolve_vscode_cli", lambda: None)
    monkeypatch.setattr(credentials, "list_workspace_summaries", AsyncMock(return_value=[]))

    probes = await _collect_all_probes()
    probe_ids = [p["id"] for p in probes]
    assert "github_cli" in probe_ids
    assert "jules_cli" in probe_ids
    assert "jules_api" in probe_ids
    assert "codex_cli" in probe_ids
    assert "claude_code_cli" in probe_ids
    assert "cline" in probe_ids
    assert "gemini_cli" in probe_ids
    assert "ollama" in probe_ids
    assert "anthropic_api" in probe_ids


@pytest.mark.asyncio
async def test_get_credentials_endpoint_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/credentials returns cached payload on repeat within TTL."""
    monkeypatch.setattr(credentials, "_require_local_request", lambda req: None)

    # Prime cache directly
    cached_payload = {
        "probes": [{"id": "cached_probe", "usable": True}],
        "summary": {"total": 1, "ready": 1, "not_ready": 0},
        "probed_at": "2026-08-30T00:00:00Z",
    }
    cache_set("credentials_probe", cached_payload)

    data = await get_credentials(_mock_request())
    assert data == cached_payload


@pytest.mark.asyncio
async def test_get_cline_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/cline/status returns structured status."""
    monkeypatch.setattr(credentials, "_require_local_request", lambda req: None)

    def mock_probe_cline() -> dict[str, Any]:
        return {
            "id": "cline",
            "installed": True,
            "authenticated": True,
            "status": "ready",
            "detail": "Ready (cline auth)",
        }

    monkeypatch.setattr(credentials, "_probe_cline", mock_probe_cline)

    data = await get_cline_status(_mock_request())
    assert data["status"] == "ready"
    assert data["authenticated"] is True
    assert data["detail"] == "Ready (cline auth)"


@pytest.mark.asyncio
async def test_vscode_probe_helpers(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(credentials, "_resolve_vscode_cli", lambda: "/usr/bin/code")
    monkeypatch.setattr(credentials, "_should_probe_vscode_cli", lambda bin: True)

    def mock_run_error(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.SubprocessError("VSCode CLI error")

    monkeypatch.setattr(subprocess, "run", mock_run_error)
    with caplog.at_level(logging.WARNING):
        has_ext = await _vscode_has_extension("test.ext")
        ext_installed = await _vscode_extension_installed("test.ext", "/usr/bin/code")

    assert has_ext is None
    assert ext_installed is False
    assert any(
        "VS Code extension probe failed for extension_id=test.ext: SubprocessError" in rec.message
        for rec in caplog.records
    )
    assert any(
        "VS Code CLI extension probe failed for extension_id=test.ext: SubprocessError" in rec.message
        for rec in caplog.records
    )


def test_env_present_anywhere_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    unreadable_file = tmp_path / "env_file"
    unreadable_file.write_text("SOME_KEY=123", encoding="utf-8")
    monkeypatch.setattr(credentials, "_MAXWELL_ENV", unreadable_file)
    monkeypatch.setattr(credentials, "_DASHBOARD_ENV", tmp_path / "nonexistent")
    monkeypatch.delenv("SOME_KEY", raising=False)

    def mock_read_text(*args: Any, **kwargs: Any) -> str:
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "read_text", mock_read_text)
    with caplog.at_level(logging.DEBUG):
        found = _env_present_anywhere("SOME_KEY")
    assert found is False
    assert any("Failed reading env file" in rec.message and "OSError" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_ollama_status_and_models(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(credentials, "_require_local_request", lambda req: None)
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/ollama" if cmd == "ollama" else None)

    req = _mock_request()

    # Success case for ollama status
    def mock_run_ps(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run_ps)
    status_ok = await get_ollama_status(req)
    assert status_ok["running"] is True

    # Error case for ollama status
    def mock_run_ps_err(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("ps error")

    monkeypatch.setattr(subprocess, "run", mock_run_ps_err)
    with caplog.at_level(logging.WARNING):
        status_err = await get_ollama_status(req)
    assert status_err["running"] is False
    assert any("Ollama status probe failed: OSError" in rec.message for rec in caplog.records)

    # Success case for ollama models
    def mock_run_models(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="NAME\tID\tSIZE\tMODIFIED\nllama3:latest\t123\t4GB\tnow\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_run_models)
    models_ok = await get_ollama_models(req)
    assert models_ok["models"] == ["llama3:latest"]

    # Failure case for ollama models
    def mock_run_models_err(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.SubprocessError("models error")

    monkeypatch.setattr(subprocess, "run", mock_run_models_err)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc_info:
            await get_ollama_models(req)
        assert exc_info.value.status_code == 500
    assert any("Failed to list ollama models: SubprocessError" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_launch_auth_handling(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(credentials, "_require_local_request", lambda req: None)
    req = _mock_request()

    # Invalid provider
    with pytest.raises(HTTPException) as exc_info:
        await launch_auth(LaunchAuthRequest(provider="invalid-provider"), req)
    assert exc_info.value.status_code == 422

    # Binary not found
    def mock_popen_not_found(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("claude binary not found")

    monkeypatch.setattr(subprocess, "Popen", mock_popen_not_found)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc_info:
            await launch_auth(LaunchAuthRequest(provider="claude"), req)
        assert exc_info.value.status_code == 502
    assert any(
        "launch_auth binary not found for provider claude: FileNotFoundError" in rec.message for rec in caplog.records
    )


def test_all_functions_in_credentials_router_under_50_lines() -> None:
    """Design by Contract check: Ensure every function in credentials.py is <= 50 lines."""
    credentials_path = _BACKEND / "routers" / "credentials.py"
    with open(credentials_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    violating_functions: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = node.end_lineno - node.lineno + 1 if node.end_lineno else 0
            if length > 50:
                violating_functions.append(f"{node.name} ({length} lines)")

    assert not violating_functions, f"Functions exceeding 50 lines: {violating_functions}"
