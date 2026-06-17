"""Configuration constants for runner-dashboard.

This package collects the static configuration surface the dashboard reads
at boot. Top-level names (``ORG``, ``HOSTNAME``, ``DISK_WARN_PERCENT``, etc.)
remain importable as ``from dashboard_config import ...`` for backwards
compatibility with the rest of the backend.

Newer constant groups live in dedicated submodules:

- :mod:`dashboard_config.cache_ttls` — per-endpoint cache TTL values.
- :mod:`dashboard_config.timeouts` — HTTP/subprocess timeouts, concurrency
  caps, and resource pressure thresholds.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import secrets
import tempfile
from pathlib import Path

from dashboard_config.cache_ttls import CacheTtl
from dashboard_config.timeouts import Concurrency, HttpTimeout, ResourceThreshold

log = logging.getLogger("dashboard")

# Paths
BACKEND_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_ROOT = BACKEND_DIR.parent
REPO_ROOT = Path(os.environ.get("RUNNER_DASHBOARD_REPO_ROOT", BACKEND_DIR.parents[0]))
RUNNER_BASE_DIR = Path(
    os.environ.get(
        "RUNNER_BASE_DIR",
        str(Path.home() / "actions-runners"),
    )
).expanduser()
RUNNER_WINDOWS_HOST_PATH = os.environ.get("RUNNER_WINDOWS_HOST_PATH", "").strip()

# GitHub Org
ORG = os.environ.get("GITHUB_ORG", "D-sorganization")

# Runner Limits
DEFAULT_NUM_RUNNERS = 12
REQUESTED_NUM_RUNNERS = int(os.environ.get("NUM_RUNNERS", str(DEFAULT_NUM_RUNNERS)))
MAX_RUNNERS = int(os.environ.get("MAX_RUNNERS", str(REQUESTED_NUM_RUNNERS)))
NUM_RUNNERS = min(REQUESTED_NUM_RUNNERS, MAX_RUNNERS)

# Runner Aliases (for machine name normalization)
RUNNER_ALIASES = [a.strip().lower() for a in os.environ.get("RUNNER_ALIASES", "").split(",") if a.strip()]

# Disk Thresholds (env-overridable; defaults sourced from ResourceThreshold)
DISK_WARN_PERCENT = float(
    os.environ.get("DASHBOARD_DISK_WARN_PERCENT", str(ResourceThreshold.DISK_WARN_PERCENT)),
)
DISK_CRITICAL_PERCENT = float(
    os.environ.get("DASHBOARD_DISK_CRITICAL_PERCENT", str(ResourceThreshold.DISK_CRITICAL_PERCENT)),
)
DISK_MIN_FREE_GB = float(
    os.environ.get("DASHBOARD_DISK_MIN_FREE_GB", str(ResourceThreshold.DISK_MIN_FREE_GB)),
)

# API / Port
PORT = int(os.environ.get("DASHBOARD_PORT", "8321"))
HOSTNAME = os.environ.get("DISPLAY_NAME") or platform.node()


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean-ish env var (1/true/yes/on → True)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Issue #930: the dashboard documents a plain-HTTP-over-tailnet deployment, but
# the session cookie was unconditionally `https_only=True` (Secure) and HSTS was
# always sent. Browsers DROP Secure cookies on http:// origins, so session auth
# silently never worked in the documented mode — pushing operators toward the
# unauthenticated paths. TLS-only protections are now gated on DASHBOARD_TLS:
# unset (default) = HTTP-mode (no Secure cookie, no HSTS); set = TLS-mode.
TLS_ENABLED = _env_flag("DASHBOARD_TLS", default=False)


def _resolve_bind_host() -> str:
    """Return the interface uvicorn should bind for incoming HTTP.

    Default is ``0.0.0.0`` (historical, binds all interfaces).
    Operators on WSL-mirrored hosts should set ``DASHBOARD_HOST=127.0.0.1``
    to avoid colliding with a Windows-side Tailscale-serve listener that
    has already claimed the Tailscale IP on the same port.

    Postcondition: returns a non-empty string; whitespace-only values fall
    back to the default so uvicorn never receives an invalid bind target.
    """
    raw = os.environ.get("DASHBOARD_HOST", "").strip()
    return raw or "0.0.0.0"


HOST = _resolve_bind_host()
# Issue #959: Maxwell-Daemon serves on 8080 by default (its cli/main.py,
# launcher.py, and CLI clients all default to http://127.0.0.1:8080). The old
# 8322 default appeared nowhere in Maxwell_Daemon and — worse — collided with the
# ControlTower-SSD pool's own dashboard_url:8322 in backend/machine_registry.yml,
# so a default deploy probed a second dashboard and misreported it as Maxwell.
# Align RD's default to the daemon's real port so an out-of-box RD reaches an
# out-of-box MD on the same host. Override with MAXWELL_PORT / MAXWELL_URL.
MAXWELL_PORT = int(os.environ.get("MAXWELL_PORT", "8080"))
MAXWELL_URL = (os.environ.get("MAXWELL_URL", "") or f"http://localhost:{MAXWELL_PORT}").rstrip("/")
# True when the operator has explicitly pointed RD at a Maxwell endpoint (either
# MAXWELL_URL or MAXWELL_PORT set). When neither is set the dashboard falls back
# to the localhost:8080 default, which is correct for a co-located daemon but is
# a guess for a remote one — the Maxwell tab surfaces a "configuration needed"
# hint instead of an opaque connection error in that case (issue #959).
MAXWELL_EXPLICITLY_CONFIGURED = bool(
    os.environ.get("MAXWELL_URL", "").strip() or os.environ.get("MAXWELL_PORT", "").strip()
)
# Issue #926: no hardcoded default secret. When MAXWELL_API_TOKEN is unset the
# dashboard sends NO Authorization header (routers.maxwell._maxwell_headers), which
# is correct for a token-less Maxwell-Daemon (per its ConnectionProfile, the daemon
# runs open only when no auth is configured). A published default string would let
# anyone reading the source mint valid Maxwell bearer tokens.
MAXWELL_API_TOKEN = os.environ.get("MAXWELL_API_TOKEN", "").strip()


def runner_limit() -> int:
    """Return the hard runner capacity this dashboard is allowed to manage."""
    return max(NUM_RUNNERS, MAX_RUNNERS)


MACHINE_ROLE = os.environ.get("MACHINE_ROLE", "node")
HUB_URL = os.environ.get("HUB_URL")
if HUB_URL:
    HUB_URL = HUB_URL.rstrip("/")

# Fleet topology
FLEET_NODES: dict[str, str] = {}
_nodes_raw = os.environ.get("FLEET_NODES", "")
if _nodes_raw:
    for pair in _nodes_raw.split(","):
        if ":" in pair:
            name, url = pair.split(":", 1)
            FLEET_NODES[name.strip()] = url.strip().rstrip("/")

# Cache / UI Limits
RUN_JOB_ENRICHMENT_LIMIT = int(os.environ.get("RUN_JOB_ENRICHMENT_LIMIT", "50"))
MAX_CACHE_SIZE = 500
CACHE_EVICT_BATCH = 50
DEFAULT_CACHE_TTL = float(os.environ.get("DASHBOARD_CACHE_TTL", "30"))

# CPU history ring-buffer depth (one sample per /api/system poll; 60 ≈ 1 min at 1 Hz)
CPU_HISTORY_MAXLEN = int(os.environ.get("DASHBOARD_CPU_HISTORY_MAXLEN", "60"))

assert MAX_CACHE_SIZE > 0, "MAX_CACHE_SIZE must be positive"
assert CPU_HISTORY_MAXLEN > 0, "CPU_HISTORY_MAXLEN must be positive"

# Scheduler / Services
RUNNER_SCHEDULER_BIN = os.environ.get("RUNNER_SCHEDULER_BIN", "/usr/local/bin/runner-scheduler")
RUNNER_SCHEDULER_SERVICE = os.environ.get("RUNNER_SCHEDULER_SERVICE", "runner-scheduler.service")
RUNNER_SCHEDULER_APPLY_CMD = os.environ.get("RUNNER_SCHEDULER_APPLY_CMD", "")
SYSTEMCTL_BIN = os.environ.get("SYSTEMCTL_BIN") or "/usr/bin/systemctl"
RUNNER_SCHEDULER_STATE = Path(os.environ.get("RUNNER_SCHEDULER_STATE", "/var/lib/runner-scheduler/state.json"))
RUNNER_SCHEDULE_CONFIG = Path(os.environ.get("RUNNER_SCHEDULE_CONFIG", "/etc/runner-scheduler/schedule.json"))

WSL_KEEPALIVE_SERVICE = os.environ.get("WSL_KEEPALIVE_SERVICE", "wsl-runner-keepalive.service")
WSL_KEEPALIVE_TASK_NAME = os.environ.get("WSL_KEEPALIVE_TASK_NAME", "WSL-Runner-KeepAlive")


def runner_scheduler_apply_command() -> list[str]:
    """Return the command to apply the runner schedule."""
    if RUNNER_SCHEDULER_APPLY_CMD:
        return RUNNER_SCHEDULER_APPLY_CMD.split()
    return [RUNNER_SCHEDULER_BIN, "apply", "--config", str(RUNNER_SCHEDULE_CONFIG)]


def _read_repo_version(version_path: Path | None = None) -> str:
    """Return the first semver entry from the repository VERSION file."""
    version_file = version_path or DASHBOARD_ROOT / "VERSION"
    for raw_line in version_file.read_text(encoding="utf-8").splitlines():
        candidate = raw_line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if re.fullmatch(r"\d+\.\d+\.\d+", candidate):
            return candidate
        break
    raise RuntimeError(f"{version_file} must contain a MAJOR.MINOR.PATCH version")


# Deployment
VERSION = _read_repo_version()
DEPLOYMENT_FILE = Path(os.environ.get("RUNNER_DASHBOARD_DEPLOYMENT_FILE", BACKEND_DIR.parent / "deployment.json"))
EXPECTED_VERSION_FILE = Path(os.environ.get("RUNNER_DASHBOARD_EXPECTED_VERSION_FILE", BACKEND_DIR.parent / "VERSION"))

# LLM
DEFAULT_LLM_MODEL = os.environ.get("DASHBOARD_LLM_MODEL", "claude-haiku-4-5-20251001")

# Heavy Test Repos
HEAVY_TEST_REPOS = {
    "Repository_Management": {
        "workflow_file": "ci-heavy-integration-tests.yml",
        "description": "Heavy Integration Suite",
        "docker_compose": "docker-compose.yml",
        "python_versions": ["3.11", "3.12"],
        "default_python": "3.12",
    },
}

# Session
# Request-log filter: paths sampled at 1/10 instead of fully suppressed.
# Errors (4xx/5xx) are always logged regardless of this list.
# Override via the LOG_FILTER_PATHS env var (comma-separated path prefixes).
_log_filter_raw = os.environ.get(
    "LOG_FILTER_PATHS",
    "/api/scheduled-workflows,/api/heavy-tests,/api/reports",
)
LOG_FILTER_PATHS: tuple[str, ...] = tuple(p.strip() for p in _log_filter_raw.split(",") if p.strip())

_SESSION_SECRET_DIR = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_SESSION_SECRET_DIR",
        Path.home() / ".config" / "runner-dashboard",
    )
)
_SESSION_SECRET_FILE = _SESSION_SECRET_DIR / "session_secret"


def _resolve_session_secret() -> tuple[str, str]:
    """Return (secret, source) where source is 'env', 'persisted', or 'generated'.

    Resolution order:
    1. ``SESSION_SECRET`` env var — source ``"env"``.
    2. Persisted file at ``~/.config/runner-dashboard/session_secret`` — source ``"persisted"``.
    3. Generate a new secret, write it atomically with mode 0o600 — source ``"generated"``.

    A WARNING is logged when the env var is absent so operators know which
    mode the server is running in.
    """
    env_val = os.environ.get("SESSION_SECRET")
    if env_val:
        return env_val, "env"

    # Try to load an already-persisted secret.
    if _SESSION_SECRET_FILE.exists():
        try:
            persisted = _SESSION_SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(persisted) >= 32:
                log.warning(
                    "SESSION_SECRET not set; reusing persisted secret from %s",
                    _SESSION_SECRET_FILE,
                )
                return persisted, "persisted"
        except OSError:
            pass  # Fall through to generate a new one.

    # Generate, persist, and warn.
    log.warning(
        "SESSION_SECRET not set; persisting to %s",
        _SESSION_SECRET_FILE,
    )
    new_secret = secrets.token_hex(32)
    _SESSION_SECRET_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write via temp file + rename so partial writes are never visible.
    fd, tmp_path_str = tempfile.mkstemp(dir=_SESSION_SECRET_DIR, prefix=".tmp-session_secret-")
    try:
        os.chmod(tmp_path_str, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_secret)
        os.replace(tmp_path_str, _SESSION_SECRET_FILE)
        _SESSION_SECRET_FILE.chmod(0o600)
    except OSError:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
    return new_secret, "generated"


SESSION_SECRET, SESSION_SECRET_SOURCE = _resolve_session_secret()


__all__ = [
    "BACKEND_DIR",
    "CACHE_EVICT_BATCH",
    "DASHBOARD_ROOT",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_NUM_RUNNERS",
    "DEPLOYMENT_FILE",
    "DISK_CRITICAL_PERCENT",
    "DISK_MIN_FREE_GB",
    "DISK_WARN_PERCENT",
    "EXPECTED_VERSION_FILE",
    "FLEET_NODES",
    "HEAVY_TEST_REPOS",
    "HOST",
    "HOSTNAME",
    "HUB_URL",
    "MACHINE_ROLE",
    "MAX_CACHE_SIZE",
    "MAXWELL_API_TOKEN",
    "MAXWELL_EXPLICITLY_CONFIGURED",
    "MAXWELL_PORT",
    "MAXWELL_URL",
    "MAX_RUNNERS",
    "NUM_RUNNERS",
    "ORG",
    "PORT",
    "REPO_ROOT",
    "REQUESTED_NUM_RUNNERS",
    "RUN_JOB_ENRICHMENT_LIMIT",
    "RUNNER_ALIASES",
    "RUNNER_BASE_DIR",
    "RUNNER_SCHEDULER_APPLY_CMD",
    "RUNNER_SCHEDULER_BIN",
    "RUNNER_SCHEDULER_SERVICE",
    "RUNNER_SCHEDULER_STATE",
    "RUNNER_SCHEDULE_CONFIG",
    "SESSION_SECRET",
    "SESSION_SECRET_SOURCE",
    "SYSTEMCTL_BIN",
    "TLS_ENABLED",
    "VERSION",
    "WSL_KEEPALIVE_SERVICE",
    "WSL_KEEPALIVE_TASK_NAME",
    "CacheTtl",
    "Concurrency",
    "HttpTimeout",
    "ResourceThreshold",
    "runner_limit",
    "runner_scheduler_apply_command",
]
