"""Credentials probe router.

Exposes read-only probe endpoint (GET /api/credentials) and a key-management
endpoint (POST /api/credentials/set-key) that lets the dashboard write API keys
to the server-side env files without exposing the values back to the browser.

Only accessible from localhost.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

router = APIRouter(prefix="/api", tags=["credentials"])

log = logging.getLogger("dashboard.credentials")

# env-file paths
_MAXWELL_ENV = Path.home() / ".config" / "maxwell-daemon" / "env"
_DASHBOARD_ENV = Path.home() / ".config" / "runner-dashboard" / "env"

# Allowed provider -> env var name mapping. Only these can be set via the API.
_PROVIDER_KEY_MAP: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "claude_code_cli": "ANTHROPIC_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
    "codex_cli": "OPENAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "gemini_cli": "GOOGLE_API_KEY",
    "jules": "JULES_API_KEY",
    "jules_api": "JULES_API_KEY",
}


def _env_present(key: str) -> bool:
    val = os.environ.get(key, "")
    return bool(val and val.strip())


def _env_source(key: str) -> str:
    return "env_var" if os.environ.get(key, "") else "unavailable"


def _require_local_request(request: Request) -> None:
    """Enforce that the request originates from localhost (issue #45)."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="This endpoint is only accessible locally")


def _write_env_var(env_file: Path, key: str, value: str) -> None:
    """Upsert KEY=value in an env file. Creates the file (and parents) if needed."""
    env_file.parent.mkdir(parents=True, exist_ok=True)
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    existing_lines = text.splitlines(keepends=True)
    pattern = re.compile(r"^" + re.escape(key) + r"=.*$", re.MULTILINE)
    filtered = [ln for ln in existing_lines if not pattern.match(ln)]
    if filtered and not filtered[-1].endswith("\n"):
        filtered[-1] += "\n"
    filtered.append(f"{key}={value}\n")
    env_file.write_text("".join(filtered), encoding="utf-8")


def _clear_env_var(env_file: Path, key: str) -> None:
    """Remove all KEY= lines from an env file."""
    if not env_file.exists():
        return
    pattern = re.compile(r"^" + re.escape(key) + r"=.*\n?", re.MULTILINE)
    text = env_file.read_text(encoding="utf-8")
    env_file.write_text(pattern.sub("", text), encoding="utf-8")


# Pydantic models

class SetKeyRequest(BaseModel):
    provider: str = Field(..., description="Provider id, e.g. 'claude', 'gemini', 'codex'")
    key: str = Field(..., min_length=1, description="The API key value (never logged)")
    restart_maxwell: bool = Field(default=True, description="Restart maxwell-daemon after saving")


class ClearKeyRequest(BaseModel):
    provider: str = Field(..., description="Provider id whose key should be removed")
    restart_maxwell: bool = Field(default=True)


@router.get("/credentials")
async def get_credentials(request: Request) -> dict:
    """Probe provider credential and connectivity state. Never exposes secret values."""
    _require_local_request(request)
    probes: list[dict] = []

    # GitHub CLI
    gh_binary = shutil.which("gh")
    gh_auth_ok = False
    gh_auth_detail = "gh not found"
    if gh_binary:
        try:
            _excluded = {"SECRET", "PASSWORD", "ANTHROPIC_API_KEY", "DASHBOARD_API_KEY"}
            _safe_env = {k: v for k, v in os.environ.items() if not any(exc in k.upper() for exc in _excluded)}
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                env=_safe_env,
            )
            gh_auth_ok = result.returncode == 0
            gh_auth_detail = "authenticated" if gh_auth_ok else "not logged in"
        except Exception:
            gh_auth_detail = "probe failed"

    probes.append({
        "id": "github_cli",
        "label": "GitHub CLI",
        "icon": "github",
        "installed": gh_binary is not None,
        "authenticated": gh_auth_ok,
        "reachable": gh_auth_ok,
        "usable": gh_auth_ok,
        "status": ("ready" if gh_auth_ok else ("not_authed" if gh_binary else "not_installed")),
        "detail": gh_auth_detail,
        "config_source": "system" if gh_binary else "unavailable",
        "docs_url": "https://cli.github.com/",
        "setup_hint": "Run: gh auth login",
    })

    # Jules CLI
    jules_binary = shutil.which("jules")
    probes.append({
        "id": "jules_cli",
        "label": "Jules CLI",
        "icon": "jules",
        "installed": jules_binary is not None,
        "authenticated": jules_binary is not None,
        "reachable": jules_binary is not None,
        "usable": jules_binary is not None,
        "status": "ready" if jules_binary else "not_installed",
        "detail": (f"Found at {jules_binary}" if jules_binary else "jules not found on PATH"),
        "config_source": "system" if jules_binary else "unavailable",
        "docs_url": "https://jules.google/docs/",
        "setup_hint": "Install Jules CLI from jules.google",
    })

    # Jules API
    jules_api_key = _env_present("JULES_API_KEY") or _env_present("GOOGLE_API_KEY")
    probes.append({
        "id": "jules_api",
        "label": "Jules API",
        "icon": "jules",
        "installed": True,
        "authenticated": jules_api_key,
        "reachable": jules_api_key,
        "usable": jules_api_key,
        "status": "ready" if jules_api_key else "missing_key",
        "detail": ("API key present" if jules_api_key else "JULES_API_KEY or GOOGLE_API_KEY not set"),
        "config_source": (_env_source("JULES_API_KEY") if jules_api_key else "unavailable"),
        "docs_url": "https://jules.google/docs/api/",
        "setup_hint": "Set JULES_API_KEY environment variable",
        "key_provider": "jules",
    })

    # Codex CLI
    codex_binary = shutil.which("codex")
    openai_key = _env_present("OPENAI_API_KEY")
    probes.append({
        "id": "codex_cli",
        "label": "Codex CLI",
        "icon": "openai",
        "installed": codex_binary is not None,
        "authenticated": openai_key,
        "reachable": codex_binary is not None and openai_key,
        "usable": codex_binary is not None and openai_key,
        "status": (
            "ready" if (codex_binary and openai_key)
            else ("missing_key" if codex_binary else "not_installed")
        ),
        "detail": (
            "Ready" if (codex_binary and openai_key)
            else ("OPENAI_API_KEY not set" if codex_binary else "codex not found on PATH")
        ),
        "config_source": (
            _env_source("OPENAI_API_KEY") if openai_key else ("system" if codex_binary else "unavailable")
        ),
        "docs_url": "https://github.com/openai/codex",
        "setup_hint": "npm install -g @openai/codex then set OPENAI_API_KEY",
        "key_provider": "codex",
    })

    # Claude Code CLI
    claude_binary = shutil.which("claude")
    anthropic_key = _env_present("ANTHROPIC_API_KEY")
    probes.append({
        "id": "claude_code_cli",
        "label": "Claude Code CLI",
        "icon": "anthropic",
        "installed": claude_binary is not None,
        "authenticated": anthropic_key,
        "reachable": claude_binary is not None and anthropic_key,
        "usable": claude_binary is not None and anthropic_key,
        "status": (
            "ready" if (claude_binary and anthropic_key)
            else ("missing_key" if claude_binary else "not_installed")
        ),
        "detail": (
            "Ready" if (claude_binary and anthropic_key)
            else ("ANTHROPIC_API_KEY not set" if claude_binary else "claude not found on PATH")
        ),
        "config_source": (
            _env_source("ANTHROPIC_API_KEY") if anthropic_key else ("system" if claude_binary else "unavailable")
        ),
        "docs_url": "https://docs.anthropic.com/claude-code",
        "setup_hint": "npm install -g @anthropic-ai/claude-code then set ANTHROPIC_API_KEY",
        "key_provider": "claude",
    })

    # Cline (VS Code extension)
    cline_config = Path.home() / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
    cline_installed = cline_config.exists()
    probes.append({
        "id": "cline",
        "label": "Cline (VS Code)",
        "icon": "vscode",
        "installed": cline_installed,
        "authenticated": cline_installed,
        "reachable": cline_installed,
        "usable": cline_installed,
        "status": "ready" if cline_installed else "not_installed",
        "detail": ("VS Code extension data found" if cline_installed else "Cline VS Code extension not found"),
        "config_source": "vscode" if cline_installed else "unavailable",
        "docs_url": "https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev",
        "setup_hint": "Install Cline extension in VS Code",
    })

    # Gemini CLI
    gemini_binary = shutil.which("gemini")
    google_key = _env_present("GOOGLE_API_KEY")
    probes.append({
        "id": "gemini_cli",
        "label": "Gemini CLI",
        "icon": "google",
        "installed": gemini_binary is not None,
        "authenticated": google_key,
        "reachable": gemini_binary is not None and google_key,
        "usable": gemini_binary is not None and google_key,
        "binary_found": gemini_binary is not None,
        "key_status": "set" if google_key else "missing",
        "status": (
            "ready" if (gemini_binary and google_key)
            else ("missing_key" if gemini_binary else "not_installed")
        ),
        "detail": (
            "Ready" if (gemini_binary and google_key)
            else ("GOOGLE_API_KEY not set" if gemini_binary else "gemini not found on PATH")
        ),
        "config_source": (
            _env_source("GOOGLE_API_KEY") if google_key else ("system" if gemini_binary else "unavailable")
        ),
        "docs_url": "https://aistudio.google.com/apikey",
        "setup_hint": "npm install -g @google/gemini-cli then set GOOGLE_API_KEY",
        "key_provider": "gemini",
    })

    # Ollama
    ollama_binary = shutil.which("ollama")
    probes.append({
        "id": "ollama_local",
        "label": "Ollama (Local)",
        "icon": "ollama",
        "installed": ollama_binary is not None,
        "authenticated": True,
        "reachable": ollama_binary is not None,
        "usable": ollama_binary is not None,
        "status": "ready" if ollama_binary else "not_installed",
        "detail": (f"Found at {ollama_binary}" if ollama_binary else "ollama not found on PATH"),
        "config_source": "system" if ollama_binary else "unavailable",
        "docs_url": "https://ollama.com/",
        "setup_hint": "Install from ollama.com",
    })

    ready = sum(1 for p in probes if p["usable"])
    return {
        "probes": probes,
        "summary": {
            "total": len(probes),
            "ready": ready,
            "not_ready": len(probes) - ready,
        },
        "probed_at": datetime.now(UTC).isoformat(),
    }


# Key management endpoints

@router.post("/credentials/set-key")
async def set_credential_key(body: SetKeyRequest, request: Request) -> dict:
    """Write an API key to the server-side env files. Never returns the key value.

    Writes to ~/.config/maxwell-daemon/env and ~/.config/runner-dashboard/env,
    updates the current process environment, then optionally restarts maxwell-daemon.
    """
    _require_local_request(request)

    provider = body.provider.lower().strip()
    if provider not in _PROVIDER_KEY_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{provider}'. Allowed: {sorted(_PROVIDER_KEY_MAP)}",
        )

    env_var = _PROVIDER_KEY_MAP[provider]
    value = body.key.strip()
    if not value:
        raise HTTPException(status_code=422, detail="Key must not be empty")

    try:
        _write_env_var(_MAXWELL_ENV, env_var, value)
        _write_env_var(_DASHBOARD_ENV, env_var, value)
    except Exception as exc:
        log.exception("Failed to write env var %s", env_var)
        raise HTTPException(status_code=500, detail=f"Failed to write key: {exc}") from exc

    os.environ[env_var] = value
    log.info("Set %s for provider=%s (length=%d)", env_var, provider, len(value))

    restart_result: dict = {}
    if body.restart_maxwell:
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", "maxwell-daemon",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            restart_result = {
                "attempted": True,
                "success": proc.returncode == 0,
                "detail": (stdout + stderr).decode(errors="replace").strip()[:200],
            }
        except Exception as exc:
            restart_result = {"attempted": True, "success": False, "detail": str(exc)[:200]}

    return {
        "ok": True,
        "env_var": env_var,
        "provider": provider,
        "maxwell_restart": restart_result,
    }


@router.post("/credentials/clear-key")
async def clear_credential_key(body: ClearKeyRequest, request: Request) -> dict:
    """Remove an API key from the server-side env files."""
    _require_local_request(request)

    provider = body.provider.lower().strip()
    if provider not in _PROVIDER_KEY_MAP:
        raise HTTPException(status_code=422, detail=f"Unknown provider '{provider}'")

    env_var = _PROVIDER_KEY_MAP[provider]

    try:
        _clear_env_var(_MAXWELL_ENV, env_var)
        _clear_env_var(_DASHBOARD_ENV, env_var)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to clear key: {exc}") from exc

    os.environ.pop(env_var, None)
    log.info("Cleared %s for provider=%s", env_var, provider)

    restart_result: dict = {}
    if body.restart_maxwell:
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", "maxwell-daemon",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            restart_result = {
                "attempted": True,
                "success": proc.returncode == 0,
                "detail": (stdout + stderr).decode(errors="replace").strip()[:200],
            }
        except Exception as exc:
            restart_result = {"attempted": True, "success": False, "detail": str(exc)[:200]}

    return {"ok": True, "env_var": env_var, "provider": provider, "maxwell_restart": restart_result}
