"""Unit tests for credentials router refactoring (Issue #1151)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from cache_utils import cache_clear, cache_get, cache_set  # noqa: E402
from dashboard_config import CacheTtl  # noqa: E402
from routers import credentials  # noqa: E402
from routers.credentials import (  # noqa: E402
    ClearKeyRequest,
    SetKeyRequest,
    _build_credentials_summary,
    _collect_all_probes,
    _probe_claude_code_cli,
    _probe_cline,
    _probe_codex_cli,
    _probe_gemini_cli,
    _probe_github_cli,
    _probe_jules_api,
    _probe_jules_cli,
    _probe_linear_workspaces,
    _probe_ollama,
    clear_credential_key,
    get_cline_status,
    get_credentials,
    set_credential_key,
)


@pytest.fixture(autouse=True)
def _clear_cache_between_tests() -> None:
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
async def test_probe_github_cli_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)

    def mock_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("Subprocess failure")

    monkeypatch.setattr(subprocess, "run", mock_run)
    probe = await _probe_github_cli()
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["detail"] == "probe failed"


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


def test_probe_codex_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_find_binary", lambda name: "/usr/bin/codex" if name == "codex" else None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    probe = _probe_codex_cli()
    assert probe["id"] == "codex_cli"
    assert probe["installed"] is True
    assert probe["authenticated"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    probe_no_key = _probe_codex_cli()
    assert probe_no_key["installed"] is True
    assert probe_no_key["authenticated"] is False
    assert probe_no_key["usable"] is False
    assert probe_no_key["status"] == "missing_key"

    monkeypatch.setattr(credentials, "_find_binary", lambda name: None)
    probe_not_installed = _probe_codex_cli()
    assert probe_not_installed["installed"] is False
    assert probe_not_installed["usable"] is False
    assert probe_not_installed["status"] == "not_installed"


def test_probe_claude_code_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    monkeypatch.setattr(credentials, "_env_present_anywhere", lambda key: key == "ANTHROPIC_API_KEY")
    probe = _probe_claude_code_cli()
    assert probe["id"] == "claude_code_cli"
    assert probe["installed"] is True
    assert probe["authenticated"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"


@pytest.mark.asyncio
async def test_probe_cline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(credentials, "_resolve_vscode_cli", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    probe = await _probe_cline()
    assert probe["id"] == "cline"
    assert probe["installed"] is False
    assert probe["usable"] is False
    assert probe["status"] == "not_installed"

    # Simulate installed via globalStorage
    cline_storage = tmp_path / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
    cline_storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    probe_installed = await _probe_cline()
    assert probe_installed["installed"] is True
    assert probe_installed["authenticated"] is True
    assert probe_installed["usable"] is True
    assert probe_installed["status"] == "ready"


def test_probe_gemini_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/gemini" if cmd == "gemini" else None)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    probe = _probe_gemini_cli()
    assert probe["id"] == "gemini_cli"
    assert probe["installed"] is True
    assert probe["authenticated"] is True
    assert probe["usable"] is True
    assert probe["status"] == "ready"


def test_probe_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/ollama" if cmd == "ollama" else None)
    probe = _probe_ollama()
    assert probe["id"] == "ollama"
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
async def test_probe_linear_workspaces_exception_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_summaries() -> list[dict[str, Any]]:
        raise ValueError("Linear API error")

    monkeypatch.setattr(credentials, "list_workspace_summaries", mock_summaries)
    probes = await _probe_linear_workspaces()
    assert probes == []


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
async def test_set_and_clear_key_invalidates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting or clearing an API key must invalidate the credentials probe cache."""
    monkeypatch.setattr(credentials, "_require_local_request", lambda req: None)
    monkeypatch.setattr(credentials, "_MAXWELL_ENV", tmp_path / "maxwell-env")
    monkeypatch.setattr(credentials, "_DASHBOARD_ENV", tmp_path / "dashboard-env")
    monkeypatch.setattr(credentials, "_MAXWELL_YAML", tmp_path / "maxwell.yaml")

    req = _mock_request()

    cache_set("credentials_probe", {"cached": True})
    assert cache_get("credentials_probe", CacheTtl.CREDENTIALS_S) is not None

    resp_set = await set_credential_key(
        SetKeyRequest(provider="claude", key="sk-ant-new-key", restart_maxwell=False),
        req,
    )
    assert resp_set["ok"] is True
    assert cache_get("credentials_probe", CacheTtl.CREDENTIALS_S) is None

    cache_set("credentials_probe", {"cached": True})
    resp_clear = await clear_credential_key(
        ClearKeyRequest(provider="claude", restart_maxwell=False),
        req,
    )
    assert resp_clear["ok"] is True
    assert cache_get("credentials_probe", CacheTtl.CREDENTIALS_S) is None


@pytest.mark.asyncio
async def test_get_cline_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/cline/status returns structured status."""
    monkeypatch.setattr(credentials, "_require_local_request", lambda req: None)

    async def mock_probe_cline() -> dict[str, Any]:
        return {
            "id": "cline",
            "installed": True,
            "compatible_key_set": True,
            "vscode_found": True,
            "vscode_cli_probe_skipped": False,
            "detail": "Cline extension installed + compatible API key found",
        }

    monkeypatch.setattr(credentials, "_probe_cline", mock_probe_cline)

    data = await get_cline_status(_mock_request())
    assert data["status"] == "extension_installed"
    assert data["vscode_found"] is True
    assert data["compatible_key_set"] is True
