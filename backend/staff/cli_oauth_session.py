"""Cheap OAuth session validity for staff CLI providers (#2170).

Login *files* alone are insufficient: expired Google/Anthropic seats still leave
artifacts on disk. This module checks token expiry when present and otherwise runs a
short, non-interactive CLI probe (cached) using the same failure phrases staff runs
emit at runtime.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from security import safe_subprocess_env
from staff import probe_swr

log = logging.getLogger("dashboard.staff.cli_oauth_session")

_PROBE_CACHE_TTL_S = 60
_CLAUDE_REFRESH_SKEW_S = 1800.0
_CLAUDE_REFRESH_TIMEOUT_S = 25.0
# Model discovery can refresh Google OAuth and perform network I/O on cold start.
_ANTIGRAVITY_PROBE_TIMEOUT_S = 30.0
_AUTH_EXPIRED_MARKERS = (
    "authentication required",
    "authentication timed out",
    "authentication expired",
    "please visit the url to log in",
    "waiting for authentication",
    "not authenticated",
    "not signed in",
    "please run claude auth login",
    "auth login",
)


def _runner_dashboard_claude_dir() -> Path:
    return Path.home() / ".config" / "runner-dashboard" / "claude"


def resolve_claude_config_dir() -> Path:
    """Config dir for the Claude seat (runner-dashboard first, then CLI default)."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    seat = _runner_dashboard_claude_dir()
    if (seat / ".credentials.json").is_file():
        return seat
    return Path.home() / ".claude"


def resolve_antigravity_home() -> Path:
    configured = os.environ.get("ANTIGRAVITY_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".gemini"


def _expiry_epoch_from_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        return ts
    if isinstance(raw, str) and raw.strip().isdigit():
        return _expiry_epoch_from_value(int(raw.strip()))
    return None


def _find_refresh_epoch(data: Any) -> float | None:
    if isinstance(data, dict):
        for key in (
            "lastRefresh",
            "last_refresh",
            "refreshed_at",
            "updated_at",
            "token_refresh_time",
            "last_token_refresh",
        ):
            if key in data:
                found = _expiry_epoch_from_value(data[key])
                if found is not None:
                    return found
        for value in data.values():
            found = _find_refresh_epoch(value)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_refresh_epoch(item)
            if found is not None:
                return found
    return None


def _find_expiry_epoch(data: Any) -> float | None:
    if isinstance(data, dict):
        for key in ("expiresAt", "expires_at", "expiry", "expiration", "expirationTime"):
            if key in data:
                found = _expiry_epoch_from_value(data[key])
                if found is not None:
                    return found
        for value in data.values():
            found = _find_expiry_epoch(value)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_expiry_epoch(item)
            if found is not None:
                return found
    return None


def _find_refresh_token(data: Any) -> bool:
    if isinstance(data, dict):
        for key in ("refreshToken", "refresh_token"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return True
        return any(_find_refresh_token(v) for v in data.values())
    if isinstance(data, list):
        return any(_find_refresh_token(item) for item in data)
    return False


def _has_refresh_token(path: Path) -> bool:
    """True when ``path`` is a JSON file containing a non-empty refreshToken/refresh_token."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return _find_refresh_token(data)


def oauth_expired_in_file(path: Path, *, skew_seconds: float = 120.0) -> bool:
    """True when ``path`` exists and its embedded OAuth expiry is in the past."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    expiry = _find_expiry_epoch(data)
    if expiry is None:
        return False
    return expiry <= time.time() + skew_seconds


def _file_expiry_or_zero(path: Path) -> float:
    try:
        return _find_expiry_epoch(json.loads(path.read_text(encoding="utf-8"))) or 0.0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0.0


def _sync_local_claude_credentials() -> None:
    """Keep runner-dashboard Claude seat and ~/.claude/.credentials.json synced with 0o600."""
    if os.environ.get("CLAUDE_CONFIG_DIR", "").strip():
        return
    paths = (_runner_dashboard_claude_dir() / ".credentials.json", Path.home() / ".claude" / ".credentials.json")
    existing = [c for c in paths if c.is_file()]
    if not existing:
        return
    newest = max(existing, key=lambda c: (_file_expiry_or_zero(c), c.stat().st_mtime))
    for target in paths:
        if not target.is_file() and not target.parent.is_dir():
            continue
        try:
            if target != newest and (
                not target.is_file()
                or (_file_expiry_or_zero(target), target.stat().st_mtime)
                < (_file_expiry_or_zero(newest), newest.stat().st_mtime)
            ):
                target.write_bytes(newest.read_bytes())
            target.chmod(0o600)
        except OSError:
            pass


def _combined_output(proc: subprocess.CompletedProcess[str]) -> str:
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def _output_indicates_auth_expired(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker in lower for marker in _AUTH_EXPIRED_MARKERS)


def _run_probe(cmd: list[str], env: Mapping[str, str] | None = None, timeout: float = 8.0) -> tuple[bool, str]:
    """Run a non-interactive auth probe. Post: never raises."""
    try:
        proc_env = safe_subprocess_env()
        if env:
            proc_env = {**proc_env, **dict(env)}
        proc = subprocess.run(  # noqa: S603 — argv built by trusted callers
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=proc_env,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        log.debug("cli oauth probe failed for %s: %s", cmd[0], type(exc).__name__)
        return False, f"{cmd[0]} auth probe failed ({type(exc).__name__})"
    combined = _combined_output(proc)
    if proc.returncode == 0 and not _output_indicates_auth_expired(combined):
        return True, "OAuth session active"
    snippet = combined.strip().splitlines()
    detail = snippet[-1][:240] if snippet else f"{cmd[0]} auth probe exit {proc.returncode}"
    return False, detail


def _cached_probe(cache_key: str, factory: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    alive, detail = probe_swr.swr_probe(cache_key, _PROBE_CACHE_TTL_S, factory)
    return bool(alive), str(detail)


def invalidate_oauth_probe_cache() -> None:
    probe_swr.forget("cli_oauth:claude")
    probe_swr.forget("cli_oauth:antigravity")


def _claude_setup_token_configured() -> bool:
    """True when ``claude setup-token`` material is present via env (subscription seat)."""
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip())


def _refresh_claude_seat(exe: str, cfg_dir: Path) -> None:
    """Let the Claude CLI refresh a near-expiry token, then mirror the credentials."""
    try:
        proc_env = {**safe_subprocess_env(), **os.environ, "CLAUDE_CONFIG_DIR": str(cfg_dir)}
        subprocess.run(  # noqa: S603 — trusted CLI refresh probe
            [exe, "-p", "ok", "--max-turns", "1"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_CLAUDE_REFRESH_TIMEOUT_S,
            check=False,
            env=proc_env,
        )
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        log.debug("claude oauth local refresh probe failed: %s", type(exc).__name__)
    _sync_local_claude_credentials()
    probe_swr.expire("cli_oauth:claude")


def claude_oauth_session_alive(binary: str | None = None) -> tuple[bool, str]:
    """Whether the Claude Code seat on this node can run without an interactive login."""
    if _claude_setup_token_configured():
        return True, "OAuth session active (CLAUDE_CODE_OAUTH_TOKEN)"
    _sync_local_claude_credentials()
    cfg_dir = resolve_claude_config_dir()
    cred = cfg_dir / ".credentials.json"
    if not cred.is_file():
        return False, "no Claude login file"
    exe = binary or "claude"
    if oauth_expired_in_file(cred, skew_seconds=_CLAUDE_REFRESH_SKEW_S) and _has_refresh_token(cred):
        if probe_swr.on_event_loop_thread():
            # The refresh can take 25 s; never run it on the event loop.
            probe_swr.run_in_background("cli_oauth:claude:refresh", lambda: _refresh_claude_seat(exe, cfg_dir))
        else:
            _refresh_claude_seat(exe, cfg_dir)
            invalidate_oauth_probe_cache()
    if oauth_expired_in_file(cred):
        return False, "Claude OAuth token expired (credentials file)"

    def _probe() -> tuple[bool, str]:
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(resolve_claude_config_dir())}
        return _run_probe([exe, "auth", "status"], env=env)

    return _cached_probe("cli_oauth:claude", _probe)


def antigravity_oauth_session_alive(binary: str | None = None) -> tuple[bool, str]:
    """Whether the Antigravity (agy) seat on this node can run without an interactive login."""
    home = resolve_antigravity_home()
    oauth_file = home / "oauth_creds.json"
    jetski_file = home / "antigravity-cli" / "jetski_state.pbtxt"
    login_markers = (
        home / "settings.json",
        home / "antigravity-cli" / "installation_id",
        jetski_file,
    )
    if not any(p.is_file() for p in login_markers) and not oauth_file.is_file():
        return False, "no Antigravity login file"
    if oauth_expired_in_file(oauth_file) and not jetski_file.is_file() and not _has_refresh_token(oauth_file):
        return False, "Antigravity OAuth token expired"
    exe = binary or "agy"

    def _probe() -> tuple[bool, str]:
        env = {**os.environ, "ANTIGRAVITY_HOME": str(home)}
        # CLI 1.4.5 has no auth subcommand. Listing models requires saved Google
        # authentication without generating a response or consuming model quota.
        return _run_probe([exe, "models"], env=env, timeout=_ANTIGRAVITY_PROBE_TIMEOUT_S)

    return _cached_probe("cli_oauth:antigravity", _probe)


_PROVIDER_CHECKERS: dict[str, Callable[[], tuple[bool, str]]] = {
    "claude": lambda: claude_oauth_session_alive(),
    "claude_code_cli": lambda: claude_oauth_session_alive(),
    "antigravity": lambda: antigravity_oauth_session_alive(),
}


def _credential_json_paths(provider_or_credential_id: str) -> list[Path]:
    if provider_or_credential_id in ("claude", "claude_code_cli"):
        cred = resolve_claude_config_dir() / ".credentials.json"
        return [cred] if cred.is_file() else []
    if provider_or_credential_id == "antigravity":
        home = resolve_antigravity_home()
        candidates = (
            home / "antigravity-cli" / "jetski_state.pbtxt",
            home / "settings.json",
            home / "oauth_creds.json",
        )
        return [p for p in candidates if p.is_file()]
    if provider_or_credential_id == "codex":
        home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        auth = home / "auth.json"
        return [auth] if auth.is_file() else []
    return []


def oauth_has_refresh_token_for_provider(provider_or_credential_id: str) -> bool:
    """True when the provider has local OAuth refresh-token material on disk."""
    if any(_has_refresh_token(p) for p in _credential_json_paths(provider_or_credential_id)):
        return True
    if provider_or_credential_id == "antigravity":
        return (resolve_antigravity_home() / "antigravity-cli" / "jetski_state.pbtxt").is_file()
    return False


def oauth_last_refresh_epoch_for_provider(provider_or_credential_id: str) -> float | None:
    """When subscription OAuth material was last refreshed (JSON hint or file mtime)."""
    best: float | None = None
    for path in _credential_json_paths(provider_or_credential_id):
        try:
            mtime = path.stat().st_mtime
            best = mtime if best is None else max(best, mtime)
        except OSError:
            pass
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        parsed = _find_refresh_epoch(data)
        if parsed is not None:
            best = parsed if best is None else max(best, parsed)
    return best


def oauth_expiry_epoch_for_provider(provider_or_credential_id: str) -> float | None:
    """Best-effort OAuth expiry epoch for preflight warnings; ``None`` when unknown."""
    if provider_or_credential_id in ("claude", "claude_code_cli"):
        cred = resolve_claude_config_dir() / ".credentials.json"
        if cred.is_file():
            try:
                data = json.loads(cred.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return _find_expiry_epoch(data)
    if provider_or_credential_id == "antigravity":
        home = resolve_antigravity_home()
        oauth_file = home / "oauth_creds.json"
        jetski_file = home / "antigravity-cli" / "jetski_state.pbtxt"
        if oauth_file.is_file():
            try:
                data = json.loads(oauth_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            exp = _find_expiry_epoch(data)
            if exp is not None and exp <= time.time() and jetski_file.is_file():
                return None
            return exp
    if provider_or_credential_id == "codex":
        home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        auth = home / "auth.json"
        if auth.is_file():
            try:
                data = json.loads(auth.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return _find_expiry_epoch(data)
    return None


def provider_oauth_session_alive(provider_or_credential_id: str) -> tuple[bool, str]:
    """Map dashboard provider / credential id to a live OAuth session check."""
    checker = _PROVIDER_CHECKERS.get(provider_or_credential_id)
    if checker is None:
        if provider_or_credential_id == "codex":
            if os.environ.get("STAFF_E2E_HERMETIC", "").strip() == "1":
                return True, ""
            home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
            if not (home / "auth.json").is_file():
                return False, "no Codex auth.json"
            if oauth_expired_in_file(home / "auth.json"):
                return False, "Codex OAuth token expired (auth.json)"
        return True, ""
    return checker()
