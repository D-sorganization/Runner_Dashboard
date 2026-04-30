"""Configuration constants for runner-dashboard."""

from __future__ import annotations

import logging
import os
import platform
import secrets
import tempfile
from pathlib import Path

log = logging.getLogger("dashboard")

# Paths
BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ.get("RUNNER_DASHBOARD_REPO_ROOT", BACKEND_DIR.parents[1]))
RUNNER_BASE_DIR = Path.home() / "actions-runners"

# GitHub Org
ORG = os.environ.get("GITHUB_ORG", "D-sorganization")

# Runner Limits
DEFAULT_NUM_RUNNERS = 12
REQUESTED_NUM_RUNNERS = int(os.environ.get("NUM_RUNNERS", str(DEFAULT_NUM_RUNNERS)))
MAX_RUNNERS = int(os.environ.get("MAX_RUNNERS", str(REQUESTED_NUM_RUNNERS)))
NUM_RUNNERS = min(REQUESTED_NUM_RUNNERS, MAX_RUNNERS)

# Runner Aliases (for machine name normalization)
RUNNER_ALIASES = [a.strip().lower() for a in os.environ.get("RUNNER_ALIASES", "").split(",") if a.strip()]

# Disk Thresholds
DISK_WARN_PERCENT = float(os.environ.get("DASHBOARD_DISK_WARN_PERCENT", "85"))
DISK_CRITICAL_PERCENT = float(os.environ.get("DASHBOARD_DISK_CRITICAL_PERCENT", "92"))
DISK_MIN_FREE_GB = float(os.environ.get("DASHBOARD_DISK_MIN_FREE_GB", "25"))

# API / Port
PORT = int(os.environ.get("DASHBOARD_PORT", "8321"))
HOSTNAME = os.environ.get("DISPLAY_NAME") or platform.node()


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


# Deployment
VERSION = "1.2.0"
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
        os.chmod(fd, 0o600)
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
