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
from routers.linear import list_workspace_summaries
from security import safe_subprocess_env

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
    "linear": "LINEAR_API_KEY",
}


def _env_present(key: str) -> bool:
    val = os.environ.get(key, "")
    return bool(val and val.strip())


def _env_present_anywhere(key: str) -> bool:
    """Check env var in current process AND in maxwell/runner env files."""
    if _env_present(key):
        return True
    for env_file in (_MAXWELL_ENV, _DASHBOARD_ENV):
        if not env_file.exists():
            continue
        try:
            text = env_file.read_text(encoding="utf-8")
            if re.search(rf"^{re.escape(key)}=\S", text, re.MULTILINE):
                return True
        except Exception:
            pass
    return False


def _find_binary(name: str) -> str | None:
    """Search for a binary on PATH and in common installation locations."""
    # First: PATH lookup
    found = shutil.which(name)
    if found:
        return found

    # Common static paths
    static_paths = [
        Path.home() / ".npm-global" / "bin" / name,
        Path.home() / ".local" / "bin" / name,
        Path("/usr/local/bin") / name,
        Path.home() / ".cargo" / "bin" / name,
    ]

    # fnm paths: ~/.local/share/fnm/node-versions/*/installation/bin/<name>
    _fnm_base = Path.home() / ".local" / "share" / "fnm" / "node-versions"
    if _fnm_base.exists():
        for version_dir in _fnm_base.iterdir():
            if version_dir.is_dir():
                candidate = version_dir / "installation" / "bin" / name
                static_paths.append(candidate)

    for p in static_paths:
        if p.exists():
            return str(p)

    return None


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


# Maps env var name -> Maxwell YAML backend path -> api_key field to update
_MAXWELL_YAML = Path.home() / ".config" / "maxwell-daemon" / "maxwell-daemon.yaml"

# env_var -> list of backend names in maxwell YAML whose api_key should be updated
_MAXWELL_BACKEND_KEY_MAP: dict[str, list[str]] = {
    "ANTHROPIC_API_KEY": ["claude", "claude-code-cli"],
    "OPENAI_API_KEY": ["openai", "codex-cli"],
    "GOOGLE_API_KEY": ["gemini"],
}


def _patch_maxwell_yaml_api_key(env_var: str, value: str) -> None:
    """Update api_key in maxwell-daemon.yaml backends that use the given env var."""
    if not _MAXWELL_YAML.exists():
        return
    backends = _MAXWELL_BACKEND_KEY_MAP.get(env_var, [])
    if not backends:
        return
    try:
        import yaml  # type: ignore[import]

        with open(_MAXWELL_YAML) as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict) or "backends" not in cfg:
            return
        changed = False
        for backend_name in backends:
            if backend_name in cfg["backends"] and isinstance(cfg["backends"][backend_name], dict):
                cfg["backends"][backend_name]["api_key"] = value
                changed = True
        if changed:
            with open(_MAXWELL_YAML, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            log.info("Patched maxwell YAML api_key for backends %s", backends)
    except Exception:
        log.exception("Could not patch maxwell YAML api_key for env_var=%s", env_var)


def _clear_maxwell_yaml_api_key(env_var: str) -> None:
    """Reset api_key to empty string in maxwell YAML backends that used this env var."""
    _patch_maxwell_yaml_api_key(env_var, "")


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
            result = await asyncio.to_thread(
                subprocess.run,
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                env=safe_subprocess_env(),
            )
            gh_auth_ok = result.returncode == 0
            gh_auth_detail = "authenticated" if gh_auth_ok else "not logged in"
        except Exception:
            gh_auth_detail = "probe failed"

    probes.append(
        {
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
        }
    )

    # Jules CLI
    jules_binary = shutil.which("jules")
    probes.append(
        {
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
        }
    )

    # Jules API
    jules_api_key = _env_present("JULES_API_KEY") or _env_present("GOOGLE_API_KEY")
    probes.append(
        {
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
        }
    )

    # Codex CLI — check PATH, npm-global, fnm, and other common locations
    codex_binary = _find_binary("codex")
    openai_key = _env_present("OPENAI_API_KEY")
    probes.append(
        {
            "id": "codex_cli",
            "label": "Codex CLI",
            "icon": "openai",
            "installed": codex_binary is not None,
            "authenticated": openai_key,
            "reachable": codex_binary is not None and openai_key,
            "usable": codex_binary is not None and openai_key,
            "binary_found": codex_binary is not None,
            "key_status": "set" if openai_key else "missing",
            "status": (
                "ready" if (codex_binary and openai_key) else ("missing_key" if codex_binary else "not_installed")
            ),
            "detail": (
                "Ready"
                if (codex_binary and openai_key)
                else ("OPENAI_API_KEY not set" if codex_binary else "codex not on PATH or npm-global")
            ),
            "config_source": (
                _env_source("OPENAI_API_KEY") if openai_key else ("system" if codex_binary else "unavailable")
            ),
            "docs_url": "https://github.com/openai/codex",
            "setup_hint": "npm install -g @openai/codex then set OPENAI_API_KEY",
            "key_provider": "codex",
        }
    )

    # Claude Code CLI
    claude_binary = shutil.which("claude")
    anthropic_key = _env_present_anywhere("ANTHROPIC_API_KEY")
    probes.append(
        {
            "id": "claude_code_cli",
            "label": "Claude Code CLI",
            "icon": "anthropic",
            "installed": claude_binary is not None,
            "authenticated": anthropic_key,
            "reachable": claude_binary is not None and anthropic_key,
            "usable": claude_binary is not None and anthropic_key,
            "binary_found": claude_binary is not None,
            "key_status": "set" if anthropic_key else "missing",
            "status": (
                "ready" if (claude_binary and anthropic_key) else ("missing_key" if claude_binary else "not_installed")
            ),
            "detail": (
                "Ready"
                if (claude_binary and anthropic_key)
                else ("ANTHROPIC_API_KEY not set" if claude_binary else "claude not found on PATH")
            ),
            "config_source": (
                _env_source("ANTHROPIC_API_KEY") if anthropic_key else ("system" if claude_binary else "unavailable")
            ),
            "docs_url": "https://docs.anthropic.com/claude-code",
            "setup_hint": "npm install -g @anthropic-ai/claude-code then set ANTHROPIC_API_KEY",
            "key_provider": "claude",
        }
    )

    # Cline (VS Code extension) — check globalStorage path AND `code --list-extensions`
    _cline_storage = Path.home() / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
    _cline_by_path = _cline_storage.exists()
    _cline_by_ext = False
    _vscode_binary = shutil.which("code")
    if _vscode_binary and not _cline_by_path:
        try:
            _ext_result = await asyncio.to_thread(
                subprocess.run,
                ["code", "--list-extensions"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            _cline_by_ext = "saoudrizwan.claude-dev" in _ext_result.stdout
        except Exception:
            pass
    cline_installed = _cline_by_path or _cline_by_ext
    _cline_detail = (
        "VS Code extension installed (globalStorage)"
        if _cline_by_path
        else (
            "VS Code extension installed (code --list-extensions)"
            if _cline_by_ext
            else ("VS Code found but Cline not installed" if _vscode_binary else "VS Code not found")
        )
    )
    _cline_compatible_key = _env_present("ANTHROPIC_API_KEY") or _env_present("OPENAI_API_KEY")
    probes.append(
        {
            "id": "cline",
            "label": "Cline (VS Code)",
            "icon": "vscode",
            "installed": cline_installed,
            "vscode_found": _vscode_binary is not None,
            "compatible_key_set": _cline_compatible_key,
            "authenticated": cline_installed and _cline_compatible_key,
            "reachable": cline_installed,
            "usable": cline_installed and _cline_compatible_key,
            "status": (
                "ready"
                if (cline_installed and _cline_compatible_key)
                else ("extension_installed" if cline_installed else "not_installed")
            ),
            "detail": (
                "VS Code extension + compatible API key found"
                if (cline_installed and _cline_compatible_key)
                else (
                    "VS Code extension installed; set ANTHROPIC_API_KEY or OPENAI_API_KEY"
                    if cline_installed
                    else _cline_detail
                )
            ),
            "config_source": "vscode" if cline_installed else "unavailable",
            "docs_url": "https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev",
            "setup_hint": "Install Cline extension in VS Code: ext install saoudrizwan.claude-dev",
        }
    )

    # Gemini CLI
    gemini_binary = shutil.which("gemini")
    google_key = _env_present("GOOGLE_API_KEY")
    probes.append(
        {
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
                "ready" if (gemini_binary and google_key) else ("missing_key" if gemini_binary else "not_installed")
            ),
            "detail": (
                "Ready"
                if (gemini_binary and google_key)
                else ("GOOGLE_API_KEY not set" if gemini_binary else "gemini not found on PATH")
            ),
            "config_source": (
                _env_source("GOOGLE_API_KEY") if google_key else ("system" if gemini_binary else "unavailable")
            ),
            "docs_url": "https://aistudio.google.com/apikey",
            "setup_hint": "npm install -g @google/gemini-cli then set GOOGLE_API_KEY",
            "key_provider": "gemini",
        }
    )

    # Ollama
    ollama_binary = shutil.which("ollama")
    probes.append(
        {
            "id": "ollama",
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
        }
    )

    # Linear integration workspaces
    try:
        for workspace in await list_workspace_summaries():
            auth_status = workspace.get("auth_status") or "missing_env"
            workspace_id = workspace.get("id") or "linear"
            teams_filter = workspace.get("teams_filter") or ["*"]
            detail_parts = [
                f"Workspace: {workspace_id}",
                f"Teams: {', '.join(teams_filter) if isinstance(teams_filter, list) else '*'}",
            ]
            if workspace.get("default_repository"):
                detail_parts.append(f"Default repo: {workspace['default_repository']}")
            probes.append(
                {
                    "id": f"linear:{workspace_id}",
                    "label": "Linear" if workspace_id == "personal" else f"Linear ({workspace_id})",
                    "icon": "linear",
                    "installed": True,
                    "authenticated": auth_status == "ok",
                    "reachable": auth_status == "ok",
                    "usable": auth_status == "ok",
                    "binary_found": auth_status == "ok",
                    "status": "ready" if auth_status == "ok" else auth_status,
                    "detail": " | ".join(detail_parts),
                    "config_source": workspace.get("auth_kind") or "api_key",
                    "docs_url": "https://linear.app/settings/api",
                    "setup_hint": (
                        "Get a personal API key from Linear -> Settings -> API. Personal API keys begin with lin_api_."
                    ),
                    "key_provider": "linear",
                    "workspace_id": workspace_id,
                }
            )
    except Exception:
        log.exception("Failed to enumerate Linear workspace credential probes")

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


@router.get("/cline/status")
async def get_cline_status(request: Request) -> dict:
    """Return Cline extension detection status."""
    _require_local_request(request)
    _cline_storage = Path.home() / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
    _cline_by_path = _cline_storage.exists()
    _cline_by_ext = False
    _vscode_binary = shutil.which("code")
    if _vscode_binary and not _cline_by_path:
        try:
            _ext_result = await asyncio.to_thread(
                subprocess.run,
                ["code", "--list-extensions"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            _cline_by_ext = "saoudrizwan.claude-dev" in _ext_result.stdout
        except Exception:
            pass
    cline_installed = _cline_by_path or _cline_by_ext
    _compatible_key = _env_present("ANTHROPIC_API_KEY") or _env_present("OPENAI_API_KEY")
    return {
        "status": (
            "extension_installed"
            if (cline_installed and _compatible_key)
            else ("extension_installed" if cline_installed else "not_installed")
        ),
        "vscode_found": _vscode_binary is not None,
        "compatible_key_set": _compatible_key,
        "detail": (
            "Cline extension installed + compatible API key found"
            if (cline_installed and _compatible_key)
            else (
                "Cline extension installed; set ANTHROPIC_API_KEY or OPENAI_API_KEY"
                if cline_installed
                else "Cline not installed in VS Code"
            )
        ),
    }


@router.get("/ollama/status")
async def get_ollama_status(request: Request) -> dict:
    """Return whether ollama serve is running and the base URL."""
    _require_local_request(request)
    ollama_binary = shutil.which("ollama")
    if not ollama_binary:
        raise HTTPException(status_code=503, detail="ollama not found on PATH")

    # Check if ollama serve is responsive
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        running = result.returncode == 0
    except Exception:
        running = False

    return {
        "running": running,
        "base_url": "http://localhost:11434",
        "binary": ollama_binary,
    }


@router.get("/ollama/models")
async def get_ollama_models(request: Request) -> dict:
    """List installed Ollama models via `ollama list`."""
    _require_local_request(request)
    ollama_binary = shutil.which("ollama")
    if not ollama_binary:
        raise HTTPException(status_code=503, detail="ollama not found on PATH")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"ollama list failed: {result.stderr.strip()[:200]}",
            )
        # Parse output: NAME\tID\tSIZE\tMODIFIED
        models = []
        for line in result.stdout.splitlines()[1:]:  # skip header
            if not line.strip():
                continue
            parts = line.split(maxsplit=3)
            if parts:
                models.append(parts[0])
        return {"models": models}
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Failed to list ollama models")
        raise HTTPException(status_code=500, detail=f"Failed to list models: {exc}") from exc


# Key management endpoints


@router.post("/credentials/set-key")
async def set_credential_key(body: SetKeyRequest, request: Request) -> dict:
    """Write an API key to the server-side env files. Never returns the key value.

    Writes to ~/.config/maxwell-daemon/env and ~/.config/runner-dashboard/env,
    updates the current process environment, patches maxwell-daemon.yaml api_key,
    then optionally restarts maxwell-daemon.
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

    _patch_maxwell_yaml_api_key(env_var, value)

    restart_result: dict = {}
    if body.restart_maxwell:
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo",
                "systemctl",
                "restart",
                "maxwell-daemon",
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
    """Remove an API key from the server-side env files and maxwell YAML."""
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
    _clear_maxwell_yaml_api_key(env_var)
    log.info("Cleared %s for provider=%s", env_var, provider)

    restart_result: dict = {}
    if body.restart_maxwell:
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo",
                "systemctl",
                "restart",
                "maxwell-daemon",
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


class LaunchAuthRequest(BaseModel):
    provider: str = Field(..., description="Provider id to launch auth for")


@router.post("/credentials/launch-auth")
async def launch_auth(body: LaunchAuthRequest, request: Request) -> dict:
    """Launch a provider's browser auth flow in a subprocess.

    Returns immediately with a job_id; the UI can poll /status.
    """
    _require_local_request(request)
    provider_id = body.provider.strip()

    if not provider_id:
        raise HTTPException(status_code=422, detail="provider is required")

    auth_commands: dict[str, list[str]] = {
        "gemini": ["gemini", "auth", "login"],
        "gemini_cli": ["gemini", "auth", "login"],
        "claude": ["claude", "auth", "login"],
        "claude_code_cli": ["claude", "auth", "login"],
        "anthropic": ["claude", "auth", "login"],
    }

    cmd = auth_commands.get(provider_id)
    if not cmd:
        raise HTTPException(
            status_code=422,
            detail=f"provider '{provider_id}' does not support launch-auth. Allowed: {sorted(auth_commands)}",
        )

    job_id = f"{provider_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    client_host = request.client.host if request.client else "unknown"
    log.info("launch_auth: provider=%s job_id=%s by=%s", provider_id, job_id, client_host)

    # Fire-and-forget subprocess (auth flows are interactive and blocking)
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Binary not found for provider '{provider_id}'",
        ) from exc
    except Exception as exc:
        log.warning("launch_auth failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to launch auth subprocess") from exc

    return {"ok": True, "provider_id": provider_id, "job_id": job_id}
