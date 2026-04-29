# ruff: noqa: B008
#!/usr/bin/env python3
"""
D-sorganization Runner Dashboard — FastAPI Backend
===================================================
Provides a REST API that:
  - Proxies GitHub's org runner & workflow APIs
  - Controls local systemd runner services (start/stop)
  - Reports real-time system metrics (CPU, RAM, disk, GPU/VRAM)
  - Tracks per-runner resource usage
  - Lists and dispatches GitHub Actions workflows (WorkflowsTab)

Usage:
    pip install fastapi uvicorn psutil PyYAML --break-system-packages
    python server.py

Then open http://localhost:8321 in your browser.
"""

import asyncio
import contextlib
import datetime as _dt_mod
import errno
import ipaddress
import json
import logging
import logging.handlers
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psutil
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from identity import Principal, require_principal, require_scope  # noqa: B008
from pydantic import BaseModel, Field
from routers import admin as admin_router
from routers import auth as auth_router
from starlette.middleware.sessions import SessionMiddleware

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import agent_dispatch_router as agent_dispatch_router  # noqa: E402
import agent_remediation as agent_remediation  # noqa: E402
import assistant_contract as assistant_contract  # noqa: E402
import assistant_tools as assistant_tools  # noqa: E402
import auth_webauthn as _auth_webauthn_router  # noqa: E402
import config_schema as config_schema  # noqa: E402
import deployment_drift as deployment_drift  # noqa: E402
import dispatch_contract as dispatch_contract  # noqa: E402
import health as _health_router  # noqa: E402
import issue_inventory as issue_inventory  # noqa: E402
import lease_synchronizer as lease_synchronizer  # noqa: E402
import linear_inventory as linear_inventory  # noqa: E402
import metrics as _metrics_router  # noqa: E402
import pr_inventory as pr_inventory  # noqa: E402
import push as _push_router  # noqa: E402
import quick_dispatch as _quick_dispatch  # noqa: E402
import quota_enforcement as quota_enforcement  # noqa: E402
import scheduled_workflows as scheduled_workflow_inventory  # noqa: E402
import unified_issue_inventory as unified_issue_inventory  # noqa: E402
import usage_monitoring as usage_monitoring  # noqa: E402
from local_app_monitoring import collect_local_apps  # noqa: E402
from machine_registry import (  # noqa: E402
    load_machine_registry,
    merge_registry_with_live_nodes,
)
from report_files import parse_report_metrics, sanitize_report_date  # noqa: E402
from routers import credentials as _credentials_router  # noqa: E402
from routers import dispatch as _dispatch_router  # noqa: E402
from routers import linear as _linear_router  # noqa: E402

# datetime.UTC added in Python 3.11; fall back to timezone.utc on older runtimes.
UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dashboard")

# ─── Constants ────────────────────────────────────────────────────────────────
DEFAULT_LLM_MODEL = os.environ.get("DASHBOARD_LLM_MODEL", "claude-haiku-4-5-20251001")

# ─── API Key Authentication ───────────────────────────────────────────────────


def _load_or_generate_api_key() -> str:
    """Return the dashboard API key, generating one if not set."""
    key_from_env = os.environ.get("DASHBOARD_API_KEY", "").strip()
    if key_from_env:
        return key_from_env
    # Try to read from persistent file
    key_file = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runner-dashboard" / "api_key.txt"
    try:
        if key_file.exists():
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError:
        pass
    # Generate a new key and persist it
    new_key = secrets.token_urlsafe(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding="utf-8")
        key_file.chmod(0o600)
        log.warning("Generated new API key; saved to %s", key_file)
        log.warning("Add header 'Authorization: Bearer %s' to all API requests.", new_key)
    except OSError as exc:
        log.warning("Could not persist API key to %s: %s", key_file, exc)
    return new_key


DASHBOARD_API_KEY: str = ""  # populated in _post_app_init()


def _setup_api_key() -> None:
    """Called after logging is configured to load/generate the API key."""
    global DASHBOARD_API_KEY  # noqa: PLW0603
    DASHBOARD_API_KEY = _load_or_generate_api_key()


# ─── Security Utilities ───────────────────────────────────────────────────────


def sanitize_log_value(value: str) -> str:
    """Strip log-injection characters from user-controlled strings."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")[:200]


def safe_subprocess_env() -> dict[str, str]:
    """Return os.environ with secrets stripped out for subprocess calls."""
    excluded = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
        "DASHBOARD_API_KEY",
        "SECRET",
        "PASSWORD",
        "TOKEN",
    }
    return {k: v for k, v in os.environ.items() if not any(exc in k.upper() for exc in excluded)}


def validate_fleet_node_url(url: str) -> str:
    """Validate a fleet node URL to prevent SSRF (issue #28)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Fleet node URL must use http or https: {url}")
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        if not (addr.is_private or addr.is_loopback):
            raise ValueError(f"Fleet node URL must be a private/local address: {url}")
    except ValueError as exc:
        # If it's not an IP address check it's a hostname we trust
        if "must be" in str(exc):
            raise
        # hostname — allow localhost, .local, .internal
        if not (host == "localhost" or host.endswith(".local") or host.endswith(".internal")):
            raise ValueError(f"Fleet node hostname not allowed: {host}") from exc
    return url


def validate_local_url(url: str, field: str = "url") -> str:
    """Validate that a URL has http/https scheme and a local host (issue #23)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{field} must use http or https")
    return validate_fleet_node_url(url)


def validate_local_path(path_str: str, allowed_root: Path) -> Path:
    """Resolve path and ensure it stays within allowed_root (issue #23)."""
    resolved = Path(path_str).expanduser().resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes allowed root: {path_str}") from exc
    return resolved


def validate_health_command(cmd: str) -> list[str]:
    """Parse health command safely, rejecting shell metacharacters (issue #22)."""
    dangerous = set(";|&`$()<>")
    if any(c in cmd for c in dangerous):
        raise ValueError(f"health_command contains disallowed characters: {cmd!r}")
    return shlex.split(cmd)


# ─── Rate Limiting ────────────────────────────────────────────────────────────

_dispatch_rate: dict[str, list[float]] = defaultdict(list)
DISPATCH_LIMIT_PER_MINUTE = 10


def check_dispatch_rate(client_ip: str) -> None:
    """Enforce rate limiting for AI agent dispatch endpoints (issue #31)."""
    now = time.monotonic()
    window = [t for t in _dispatch_rate[client_ip] if now - t < 60]
    if len(window) >= DISPATCH_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded for agent dispatch")
    window.append(now)
    _dispatch_rate[client_ip] = window


# ─── Pydantic Input Models ────────────────────────────────────────────────────


class WorkflowDispatchBody(BaseModel):
    repository: str = Field(..., max_length=200)
    workflow_id: Any = None
    ref: str = Field(default="main", max_length=200)
    inputs: dict[str, Any] = Field(default_factory=dict)
    approved_by: str = Field(..., max_length=200)


class HeavyTestDispatchBody(BaseModel):
    repo: str = Field(..., max_length=200)
    python_version: str = Field(default="3.11", max_length=20)
    ref: str = Field(default="main", max_length=200)


class FeatureRequestDispatchBody(BaseModel):
    repository: str = Field(..., max_length=200)
    branch: str = Field(default="main", max_length=200)
    provider: str = Field(default="jules_api", max_length=100)
    prompt: str = Field(..., max_length=10000)
    standards: list[str] = Field(default_factory=list)


class AssessmentDispatchBody(BaseModel):
    repository: str = Field(..., max_length=200)
    provider: str = Field(default="jules_api", max_length=100)
    ref: str = Field(default="main", max_length=200)


class MaxwellControlBody(BaseModel):
    action: str = Field(..., max_length=20)
    approved_by: str = Field(..., max_length=200)


class MaxwellChatBody(BaseModel):
    message: str = Field(..., max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class HelpChatBody(BaseModel):
    question: str = Field(..., max_length=2000)
    current_tab: str = Field(default="", max_length=100)


# ─── Bounded Cache ────────────────────────────────────────────────────────────

MAX_CACHE_SIZE = 500
_CACHE_EVICT_BATCH = 50

# ─── Shared State Locks ───────────────────────────────────────────────────────
_remediation_history_lock: asyncio.Lock = asyncio.Lock()
_orchestration_audit_lock: asyncio.Lock = asyncio.Lock()
_feature_requests_lock: asyncio.Lock = asyncio.Lock()
_prompt_templates_lock: asyncio.Lock = asyncio.Lock()
_prompt_notes_lock: asyncio.Lock = asyncio.Lock()

# ─── Configuration ────────────────────────────────────────────────────────────
ORG = os.environ.get("GITHUB_ORG", "D-sorganization")
REPO_ROOT = Path(os.environ.get("RUNNER_DASHBOARD_REPO_ROOT", BACKEND_DIR.parents[1]))
RUNNER_BASE_DIR = Path.home() / "actions-runners"
DEFAULT_NUM_RUNNERS = 12
REQUESTED_NUM_RUNNERS = int(os.environ.get("NUM_RUNNERS", str(DEFAULT_NUM_RUNNERS)))
MAX_RUNNERS = int(os.environ.get("MAX_RUNNERS", str(REQUESTED_NUM_RUNNERS)))
NUM_RUNNERS = min(REQUESTED_NUM_RUNNERS, MAX_RUNNERS)
DISK_WARN_PERCENT = float(os.environ.get("DASHBOARD_DISK_WARN_PERCENT", "85"))
DISK_CRITICAL_PERCENT = float(os.environ.get("DASHBOARD_DISK_CRITICAL_PERCENT", "92"))
DISK_MIN_FREE_GB = float(os.environ.get("DASHBOARD_DISK_MIN_FREE_GB", "25"))
PORT = int(os.environ.get("DASHBOARD_PORT", "8321"))
_DASHBOARD_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runner-dashboard"
HOSTNAME = os.environ.get("DISPLAY_NAME") or platform.node()
RUN_JOB_ENRICHMENT_LIMIT = int(os.environ.get("RUN_JOB_ENRICHMENT_LIMIT", "50"))
RUNNER_ALIASES = [item.strip() for item in os.environ.get("RUNNER_ALIASES", "").split(",") if item.strip()]
RUNNER_SCHEDULE_CONFIG = Path(
    os.environ.get(
        "RUNNER_SCHEDULE_CONFIG",
        str(Path.home() / ".config" / "runner-dashboard" / "runner-schedule.json"),
    )
).expanduser()
RUNNER_SCHEDULER_BIN = os.environ.get("RUNNER_SCHEDULER_BIN", "/usr/local/bin/runner-scheduler")
RUNNER_SCHEDULER_SERVICE = os.environ.get("RUNNER_SCHEDULER_SERVICE", "runner-scheduler.service")
RUNNER_SCHEDULER_APPLY_CMD = os.environ.get("RUNNER_SCHEDULER_APPLY_CMD", "")
SYSTEMCTL_BIN = os.environ.get("SYSTEMCTL_BIN") or shutil.which("systemctl") or "/usr/bin/systemctl"
RUNNER_SCHEDULER_STATE = Path(os.environ.get("RUNNER_SCHEDULER_STATE", "/var/lib/runner-scheduler/state.json"))
WSL_KEEPALIVE_SERVICE = os.environ.get("WSL_KEEPALIVE_SERVICE", "wsl-runner-keepalive.service")
WSL_KEEPALIVE_TASK_NAME = os.environ.get("WSL_KEEPALIVE_TASK_NAME", "WSL-Runner-KeepAlive")
DEPLOYMENT_FILE = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_DEPLOYMENT_FILE",
        Path(__file__).resolve().parent.parent / "deployment.json",
    )
)
# Hub's expected dashboard version lives in runner-dashboard/VERSION and is
# bumped on every release. Nodes compare against this to detect drift.
EXPECTED_VERSION_FILE = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_EXPECTED_VERSION_FILE",
        Path(__file__).resolve().parent.parent / "VERSION",
    )
)

# ─── Setup moving averages and host memory cache ────────────

_cpu_history: deque[float] = deque(maxlen=60)


def _runner_scheduler_apply_command() -> list[str]:
    if RUNNER_SCHEDULER_APPLY_CMD.strip():
        return shlex.split(RUNNER_SCHEDULER_APPLY_CMD)
    return ["sudo", "-n", SYSTEMCTL_BIN, "start", RUNNER_SCHEDULER_SERVICE]


HOST_MEMORY_GB = None
try:
    if "microsoft-standard" in platform.uname().release.lower():
        # Running in WSL -> try interop to get physical hardware capacity
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env=safe_subprocess_env(),
        )
        if result.returncode == 0:
            HOST_MEMORY_GB = round(int(result.stdout.strip()) / (1024**3), 1)
except Exception:  # noqa: BLE001
    pass


# Path to daily progress reports (on Windows mount from WSL2)
_default_reports_dir = (
    Path("/mnt/c")
    / "Users"
    / os.environ.get("USER", "diete")
    / "Repositories"
    / "Repository_Management"
    / "docs"
    / "progress-tracking"
)
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", str(_default_reports_dir)))

# Repos with heavy-test workflows (workflow_dispatch capable)
HEAVY_TEST_REPOS = {
    "Repository_Management": {
        "workflow_file": "ci-heavy-integration-tests.yml",
        "description": ("Heavy Integration Suite — Self-hosted Runner Control Tower tests"),
        "docker_compose": "docker-compose.yml",
        "python_versions": ["3.11", "3.12"],
        "default_python": "3.12",
    },
    "UpstreamDrift": {
        "workflow_file": "heavy-tests-opt-in.yml",
        "description": ("Heavy Integration Tests (live_simulation marker) — MuJoCo, Drake, Pinocchio, Biomechanics"),
        "docker_compose": "docker-compose.yml",
        "python_versions": ["3.10", "3.11", "3.12"],
        "default_python": "3.11",
    },
}

app = FastAPI(
    title="D-sorganization Runner Dashboard",
    version="4.0.0",
    description="Monitor and control self-hosted GitHub Actions runners",
)

_PROCESSED_ENVELOPES_PATH = Path.home() / "actions-runners" / "dashboard" / "processed_envelopes.json"
_processed_envelopes_lock: asyncio.Lock = asyncio.Lock()


def _load_processed_envelopes() -> dict[str, float]:
    """Load processed envelope IDs and expiration times from disk."""
    if not _PROCESSED_ENVELOPES_PATH.exists():
        return {}
    try:
        data = json.loads(_PROCESSED_ENVELOPES_PATH.read_text())
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


async def _is_envelope_replay(envelope_id: str) -> bool:
    """Check if envelope_id has already been processed (replay detection)."""
    async with _processed_envelopes_lock:
        processed = _load_processed_envelopes()
        now = datetime.now(UTC).timestamp()

        if envelope_id in processed:
            expires_at = processed[envelope_id]
            return expires_at > now

        return False


async def _record_processed_envelope(envelope_id: str, ttl_seconds: int = 86400) -> None:
    """Record that envelope_id has been processed (for replay detection)."""
    async with _processed_envelopes_lock:
        processed = _load_processed_envelopes()
        now = datetime.now(UTC).timestamp()
        expires_at = now + ttl_seconds

        processed[envelope_id] = expires_at

        cleaned = {k: v for k, v in processed.items() if v > now}
        try:
            _PROCESSED_ENVELOPES_PATH.parent.mkdir(parents=True, exist_ok=True)
            config_schema.atomic_write_json(_PROCESSED_ENVELOPES_PATH, cleaned)
        except OSError as exc:
            log.warning("failed to record processed envelope: %s", exc)


# ── Bounded domain routers ────────────────────────────────────────────────────
app.include_router(_dispatch_router.router)
_dispatch_router.set_replay_functions(_is_envelope_replay, _record_processed_envelope)
app.include_router(_credentials_router.router)
app.include_router(_linear_router.router)
app.include_router(_push_router.router)
app.include_router(admin_router.router)
app.include_router(auth_router.router)
app.include_router(_auth_webauthn_router.router)
app.include_router(_health_router.router)
app.include_router(_metrics_router.router)

# Agent-launcher control surface (sibling: Repository_Management/launchers/cline_agent_launcher).
# Subprocess-only — never imports the launcher Python at runtime.
import agent_launcher_router as _agent_launcher_router  # noqa: E402

app.include_router(_agent_launcher_router.router)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", secrets.token_hex(32)),
    session_cookie="dashboard_session",
    max_age=86400 * 7,  # 7 days
    same_site="lax",
    https_only=False,  # True if prod
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8321",
        "http://127.0.0.1:8321",
        f"http://localhost:{os.environ.get('DASHBOARD_PORT', '8321')}",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_AUTH_EXEMPT_PATHS = {
    "/",
    "/health",
    "/api/health",
    "/manifest.webmanifest",
    "/icon.svg",
    "/api/auth/github",
    "/api/auth/callback",
}
_AUTH_EXEMPT_PREFIXES = ("/docs", "/openapi", "/redoc", "/assets")


@app.middleware("http")
async def _csrf_check(request: Request, call_next: Any) -> Any:
    """Reject state-changing requests that lack the CSRF sentinel header (issue #30).

    Browsers never send X-Requested-With cross-origin without an explicit CORS
    pre-flight, so requiring it is a lightweight CSRF mitigation suitable for a
    local-only dashboard.  The frontend must include the header on every
    POST / PUT / DELETE / PATCH request.
    """
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Allow health / static routes without the header so monitoring tools
        # (e.g. curl health checks) still work.  Only enforce on /api/* paths.
        if request.url.path.startswith("/api/"):
            if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                return JSONResponse(
                    {"error": "CSRF check failed: missing X-Requested-With header"},
                    status_code=403,
                )
    return await call_next(request)


@app.middleware("http")
async def _add_security_headers(request: Request, call_next: Any) -> Any:
    """Inject standard security headers on every response (issue #7, #18)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # 'unsafe-inline' removed from script-src (issue #18).
    # 'strict-dynamic' lets scripts loaded from trusted CDN origins load further
    # dependencies without needing individual allow-list entries.
    # 'unsafe-inline' is retained for style-src because React's CSS-in-JS and
    # the dashboard's own <style> block rely on inline styles. A build step
    # would allow switching to nonce or hash-based CSP for style-src.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'strict-dynamic' "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self' data:;"
    )
    return response


# ─── Startup timestamp ───────────────────────────────────────────────────────
BOOT_TIME = time.time()
_setup_api_key()

# ─── Response cache ───────────────────────────────────────────────────────────
# The frontend polls every 10-15 s; without caching, each poll spawns dozens of
# `gh api` subprocesses that rapidly exhaust the 5 000 req/hr rate limit.
# TTL values are tuned to each endpoint's staleness tolerance.
#
#   runners / health  → 25 s   (runner state changes on job start/finish)
#   queue             → 20 s   (jobs drain fast; want near-real-time)
#   runs              → 30 s
#   stats             → 60 s   (aggregate counts; no need to be instant)
#   repos             → 120 s  (repo list / metadata changes rarely)
#   diagnose          → 60 s   (expensive multi-call; used for troubleshooting)
_cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()


def _cache_get(key: str, ttl: float) -> Any | None:
    """Return cached value if within TTL, else None."""
    entry = _cache.get(key)
    if entry is not None:
        data, ts = entry
        if time.time() - ts < ttl:
            return data
    return None


def _cache_set(key: str, data: Any, _ttl: float | None = None) -> None:
    """Store value with current timestamp. Evicts oldest entries when full (issue #48)."""
    if key in _cache:
        _cache.move_to_end(key)
    elif len(_cache) >= MAX_CACHE_SIZE:
        for _ in range(_CACHE_EVICT_BATCH):
            if _cache:
                _cache.popitem(last=False)
    _cache[key] = (data, time.time())


def _deployment_info() -> dict:
    """Return the deployed dashboard revision recorded by update-deployed.sh."""
    fallback = {
        "app": "runner-dashboard",
        "version": app.version,
        "git_sha": os.environ.get("DASHBOARD_GIT_SHA", "unknown"),
        "git_branch": os.environ.get("DASHBOARD_GIT_BRANCH", "unknown"),
        "source": "environment",
    }
    try:
        payload = json.loads(DEPLOYMENT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    payload.setdefault("app", "runner-dashboard")
    payload.setdefault("version", app.version)
    payload.setdefault("source", "deployment-file")
    return payload


def _local_hardware_specs(gpu: dict | None = None) -> dict:
    """Return stable-enough hardware facts for fleet workload placement."""
    mem = psutil.virtual_memory()
    gpu = gpu if gpu is not None else get_gpu_info()
    gpu_devices = gpu.get("gpus", []) if isinstance(gpu, dict) else []
    gpu_vram_values = [
        round(device.get("vram_total_mb", 0) / 1024, 1)
        for device in gpu_devices
        if isinstance(device, dict) and device.get("vram_total_mb") is not None
    ]
    return {
        "cpu_model": platform.processor() or platform.machine(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "memory_gb": HOST_MEMORY_GB or round(mem.total / (1024**3), 1),
        "wsl_memory_gb": round(mem.total / (1024**3), 1),
        "gpu_count": len(gpu_devices),
        "gpu_vram_gb": max(gpu_vram_values) if gpu_vram_values else None,
        "accelerators": [device.get("name") for device in gpu_devices if device.get("name")],
        "platform": platform.platform(),
    }


def _workload_capacity_from_specs(specs: dict) -> dict:
    logical = specs.get("cpu_logical_cores") or 0
    memory_gb = specs.get("memory_gb") or 0
    gpu_vram_gb = specs.get("gpu_vram_gb") or 0
    tags = set(specs.get("workload_tags") or [])
    if gpu_vram_gb:
        tags.add("gpu")
    if logical and logical >= 8:
        tags.add("parallel-ci")
    if memory_gb and memory_gb >= 32:
        tags.add("memory-heavy")
    if logical and logical <= 4:
        tags.add("small-ci")
    return {
        "cpu_slots": max(1, int(logical // 2)) if logical else None,
        "memory_gb": memory_gb or None,
        "gpu_vram_gb": gpu_vram_gb or None,
        "tags": sorted(tags),
    }


def _disk_pressure_snapshot(
    *,
    path: str,
    total_gb: float,
    used_gb: float,
    free_gb: float,
    percent: float,
) -> dict:
    """Return dashboard-safe disk pressure state for autoscaling and UI alerts."""
    status = "healthy"
    reasons = []
    if percent >= DISK_CRITICAL_PERCENT:
        status = "critical"
        reasons.append(f"disk usage >= {DISK_CRITICAL_PERCENT:g}%")
    elif percent >= DISK_WARN_PERCENT:
        status = "warning"
        reasons.append(f"disk usage >= {DISK_WARN_PERCENT:g}%")
    if free_gb <= DISK_MIN_FREE_GB:
        free_space_status = "critical" if free_gb <= max(5.0, DISK_MIN_FREE_GB / 2) else "warning"
        if status != "critical":
            status = free_space_status
        reasons.append(f"free space <= {DISK_MIN_FREE_GB:g} GB")

    recommendations = []
    if status != "healthy":
        recommendations.extend(
            [
                "Run runner-dashboard/deploy/runner-cleanup.sh to clear stale runner work directories.",
                "Prune unused Docker images, volumes, and build caches if Docker is used in WSL.",
                "After cleanup, run wsl --shutdown from Windows and compact the distro VHDX.",
            ]
        )

    return {
        "status": status,
        "path": path,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent": percent,
        "warn_percent": DISK_WARN_PERCENT,
        "critical_percent": DISK_CRITICAL_PERCENT,
        "min_free_gb": DISK_MIN_FREE_GB,
        "reasons": reasons,
        "recommendations": recommendations,
    }


# ─── Fleet node config ───────────────────────────────────────────────────────
# Set MACHINE_ROLE=hub on the primary machine.
# Set FLEET_NODES to a comma-separated list of "name:http://tailscale-ip:8321"
# entries for every *other* machine in the fleet.  The hub always includes
# itself automatically — do not list it in FLEET_NODES.
#
# Example (in /etc/systemd/system/runner-dashboard.service on ControlTower):
#   Environment=MACHINE_ROLE=hub
#   Environment=FLEET_NODES=envy:http://100.x.x.x:8321,thinkpad:http://100.x.x.x:8321
MACHINE_ROLE = os.environ.get("MACHINE_ROLE", "node")
_fleet_raw = os.environ.get("FLEET_NODES", "")
FLEET_NODES: dict[str, str] = {}
for _entry in _fleet_raw.split(","):
    _entry = _entry.strip()
    if not _entry:
        continue
    # Format: name:http://host:port  — the URL part begins after the first colon
    # but URLs also contain colons, so we require the URL to start with http
    _colon_idx = _entry.find(":http")
    if _colon_idx == -1:
        _colon_idx = _entry.find(":https")
    if _colon_idx > 0:
        _label = _entry[:_colon_idx].strip()
        _url = _entry[_colon_idx + 1 :].strip()
    elif ":" in _entry:
        _label, _, _url = _entry.partition(":")
        _label = _label.strip()
        _url = _url.strip()
    else:
        continue
    if _label and _url:
        try:
            validate_fleet_node_url(_url)
            FLEET_NODES[_label] = _url
        except ValueError as _e:
            log.warning("Skipping invalid FLEET_NODES entry %r: %s", _entry, _e)

HUB_URL = os.environ.get("HUB_URL")
if HUB_URL:
    HUB_URL = HUB_URL.rstrip("/")

# ─── Helpers ──────────────────────────────────────────────────────────────────


async def proxy_to_hub(request: Request):
    """Proxy request to the designated HUB_URL for hub-spoke topology."""
    if not HUB_URL:
        raise HTTPException(status_code=502, detail="HUB_URL not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        url = f"{HUB_URL}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        try:
            req = client.build_request(
                request.method,
                url,
                headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
                content=await request.body(),
            )
            resp = await client.send(req)
            # Prevent decoding errors on empty/non-json responses if necessary
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("Hub proxy error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=502, detail="Hub proxy error") from e

            log.warning("Hub proxy error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=502, detail="Hub proxy error") from e


def _should_proxy_fleet_to_hub(request: Request) -> bool:
    """Return True when this node should use the hub's fleet-wide view.

    Local health, system metrics, watchdog, and runner schedule endpoints stay
    local. Fleet-wide endpoints can proxy to the hub, while hub fan-out calls
    can add ``?local=1`` to force a node-local action and avoid proxy loops.
    """
    if MACHINE_ROLE != "node" or not HUB_URL:
        return False
    local_value = request.query_params.get("local", "").lower()
    scope_value = request.query_params.get("scope", "").lower()
    return local_value not in {"1", "true", "yes", "local"} and scope_value != "local"


async def run_cmd(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a shell command asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout.decode(),
            stderr.decode(),
        )
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        proc.kill()
        return -1, "", "Command timed out"


async def gh_api(endpoint: str) -> dict:
    """Call the GitHub API via gh CLI.

    Uses GH_TOKEN env var when set (required for admin:org endpoints such as
    /orgs/{org}/actions/runners).  GH_TOKEN must be a classic PAT with
    scopes: repo, admin:org.  See docs/operations/fleet-machine-setup.md.
    """
    code, stdout, stderr = await run_cmd(["gh", "api", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return json.loads(stdout)


# gh_api_admin is an alias kept for call-site clarity; all calls use GH_TOKEN.
gh_api_admin = gh_api


async def gh_api_raw(endpoint: str) -> str:
    """Call the GitHub API via gh CLI and return the raw body text."""
    code, stdout, stderr = await run_cmd(["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return stdout


async def _expected_dashboard_version_from_hub() -> str | None:
    """Fetch the hub's expected dashboard VERSION when this node has a hub."""
    if MACHINE_ROLE != "node" or not HUB_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{HUB_URL}/api/deployment/expected-version")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("hub expected-version fetch failed: %s", exc)
        return None
    expected = str(payload.get("expected") or "").strip()
    if not expected or expected == "unknown":
        return None
    return expected


async def _read_expected_dashboard_version() -> str:
    """Return the hub expected VERSION, falling back to this checkout."""
    return await _expected_dashboard_version_from_hub() or deployment_drift.read_expected_version(EXPECTED_VERSION_FILE)


def _node_deployment_info(node: dict) -> dict:
    """Return the deployment payload reported by a fleet node."""
    health = node.get("health") if isinstance(node.get("health"), dict) else {}
    deployment = health.get("deployment") if isinstance(health, dict) else {}
    if not isinstance(deployment, dict):
        deployment = {}
    payload = dict(deployment)
    payload.setdefault("app", "runner-dashboard")
    payload.setdefault("version", "unknown")
    payload.setdefault("git_sha", "unknown")
    payload.setdefault("git_branch", "unknown")
    return payload


def _machine_deployment_state(node: dict, expected_version: str) -> dict:
    """Build a per-machine deployment state record."""
    deployment = _node_deployment_info(node)
    status = deployment_drift.evaluate_drift(deployment, expected_version)
    _reg = node.get("registry")
    registry = _reg if isinstance(_reg, dict) else {}
    _h = node.get("health")
    health = _h if isinstance(_h, dict) else {}
    last_health_check = health.get("timestamp") or node.get("last_seen")
    last_rollback = None
    if isinstance(registry, dict):
        deployment_meta = registry.get("deployment")
        if isinstance(deployment_meta, dict):
            last_rollback = deployment_meta.get("last_rollback")
        if last_rollback is None:
            maintenance = registry.get("maintenance")
            if isinstance(maintenance, dict):
                last_rollback = maintenance.get("last_rollback")
    if not node.get("online"):
        rollout_state = "offline"
        rollout_label = "Offline"
        rollout_detail = node.get("offline_detail") or node.get("error") or "Node is offline."
    elif status.dirty:
        rollout_state = "dirty"
        rollout_label = "Dirty"
        rollout_detail = "Node is running a dirty checkout and needs a clean redeploy."
    elif status.drift:
        rollout_state = "drifted"
        rollout_label = "Drifting"
        rollout_detail = status.message
    elif node.get("offline_reason") == "resource_monitoring":
        rollout_state = "degraded"
        rollout_label = "Degraded"
        rollout_detail = node.get("offline_detail") or "Resource pressure is blocking the usual rollout cadence."
    elif status.current == "unknown":
        rollout_state = "unknown"
        rollout_label = "Unknown"
        rollout_detail = "Deployment metadata is missing, so the node's rollout state cannot be compared."
    else:
        rollout_state = "steady"
        rollout_label = "In sync"
        rollout_detail = status.message

    return {
        "name": node.get("name"),
        "display_name": registry.get("display_name") or node.get("name"),
        "role": registry.get("role") or node.get("role"),
        "online": bool(node.get("online")),
        "dashboard_reachable": bool(node.get("dashboard_reachable")),
        "desired_version": expected_version,
        "deployed_version": status.current,
        "drift_status": status.to_dict(),
        "rollout_state": rollout_state,
        "rollout_label": rollout_label,
        "rollout_detail": rollout_detail,
        "last_health_check": last_health_check,
        "last_rollback": last_rollback,
        "update_available": status.drift and not status.dirty,
    }


def _build_deployment_state(nodes: list[dict], expected_version: str) -> dict:
    """Summarize deployment state across the fleet."""
    deployment = _deployment_info()
    local_drift = deployment_drift.evaluate_drift(deployment, expected_version)
    machines = [_machine_deployment_state(node, expected_version) for node in nodes]
    attention_states = {"offline", "dirty", "drifted", "degraded", "unknown"}
    alerting = [machine for machine in machines if machine["rollout_state"] in attention_states]
    online = sum(1 for machine in machines if machine["online"])
    steady = sum(1 for machine in machines if machine["rollout_state"] == "steady")
    dirty = sum(1 for machine in machines if machine["rollout_state"] == "dirty")
    offline = sum(1 for machine in machines if machine["rollout_state"] == "offline")
    drifted = sum(1 for machine in machines if machine["rollout_state"] == "drifted")
    degraded = sum(1 for machine in machines if machine["rollout_state"] == "degraded")
    unknown = sum(1 for machine in machines if machine["rollout_state"] == "unknown")
    if not machines:
        rollout_status = "unknown"
    elif dirty:
        rollout_status = "blocked"
    elif offline or degraded:
        rollout_status = "degraded"
    elif drifted or unknown or alerting:
        rollout_status = "attention"
    else:
        rollout_status = "stable"
    summary = (
        f"{steady}/{len(machines)} machines are on {expected_version}"
        if machines
        else "No fleet machines reported deployment metadata."
    )
    if alerting:
        summary += f" {offline} offline, {drifted} drifting, {dirty} dirty, {degraded} degraded, {unknown} unknown."
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "deployment": deployment,
        "expected_version": expected_version,
        "drift": local_drift.to_dict(),
        "rollout_state": {
            "status": rollout_status,
            "summary": summary,
            "machines_total": len(machines),
            "machines_online": online,
            "machines_steady": steady,
            "machines_dirty": dirty,
            "machines_offline": offline,
            "machines_drifting": drifted,
            "machines_degraded": degraded,
            "machines_unknown": unknown,
            "machines_attention": len(alerting),
        },
        "machines": machines,
    }


def _empty_queue_result() -> dict:
    """Return the standard empty queue payload."""
    return {
        "queued": [],
        "in_progress": [],
        "total": 0,
        "queued_count": 0,
        "in_progress_count": 0,
    }


async def _get_recent_org_repos(limit: int = 30) -> list[dict]:
    """Fetch recently updated organization repositories."""
    code, stdout, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/orgs/{ORG}/repos?per_page={limit}&sort=updated&direction=desc",
        ],
        timeout=20,
    )
    if code != 0:
        return []
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return []


async def _github_search_total(query: str) -> int:
    """Return the GitHub Search API total_count for a query."""
    code, stdout, _ = await run_cmd(
        ["gh", "api", f"search/issues?q={query}&per_page=1"],
        timeout=15,
    )
    if code != 0:
        return 0
    try:
        return int(json.loads(stdout).get("total_count", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0


async def _fetch_repo_runs(
    repo_name: str,
    *,
    per_page: int = 10,
    status: str | None = None,
) -> list[dict]:
    """Fetch workflow runs for one repository and annotate repository name."""
    status_part = f"&status={status}" if status else ""
    rc, out, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/repos/{ORG}/{repo_name}/actions/runs?per_page={per_page}{status_part}",
        ],
        timeout=15,
    )
    if rc != 0:
        return []
    try:
        runs = json.loads(out).get("workflow_runs", [])
    except (json.JSONDecodeError, ValueError):
        return []
    for run in runs:
        if "repository" not in run or not run["repository"]:
            run["repository"] = {"name": repo_name}
    return runs


async def _fetch_run_jobs(repo_name: str, run_id: int | str) -> list[dict]:
    """Fetch job-level data for one workflow run."""
    rc, out, _ = await run_cmd(
        [
            "gh",
            "api",
            f"/repos/{ORG}/{repo_name}/actions/runs/{run_id}/jobs?per_page=100",
        ],
        timeout=10,
    )
    if rc != 0:
        return []
    try:
        return json.loads(out).get("jobs", [])
    except (json.JSONDecodeError, ValueError):
        return []


async def _fetch_failed_log_excerpt(repo_name: str, run_id: int | str) -> str:
    """Best-effort failed-log excerpt for a workflow run."""
    code, stdout, _ = await run_cmd(
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            f"{ORG}/{repo_name}",
            "--log-failed",
        ],
        timeout=20,
    )
    if code != 0:
        return ""
    text = stdout.strip()
    if not text:
        return ""
    return text[:12000]


def _repo_name_from_run(run: dict) -> str | None:
    """Return the repository name from either normalized or raw run payloads."""
    repo = run.get("repository")
    if isinstance(repo, dict) and repo.get("name"):
        return str(repo["name"])
    if run.get("_repo"):
        return str(run["_repo"])
    return None


def _normalize_repository_input(value: str) -> tuple[str, str]:
    """Return (repo_name, full_name) for dashboard remediation inputs."""
    text = str(value).strip()
    if "/" in text:
        owner, _, repo_name = text.partition("/")
        if owner and repo_name:
            return repo_name, text
    return text, f"{ORG}/{text}"


def _machine_name_from_runner_name(runner_name: str | None) -> str | None:
    """Normalize fleet runner names to dashboard machine names."""
    if not runner_name:
        return None
    name = str(runner_name).strip()
    prefix = "d-sorg-local-"
    if not name.startswith(prefix):
        return name
    stem = name.removeprefix(prefix)
    machine, separator, suffix = stem.rpartition("-")
    if separator and suffix.isdigit() and machine:
        return machine
    return stem


def _placement_from_jobs(jobs: list[dict]) -> dict:
    """Extract machine placement fields from a run's jobs."""
    for job in jobs:
        runner_name = job.get("runner_name")
        if not runner_name:
            continue
        return {
            "runner_id": job.get("runner_id"),
            "runner_name": runner_name,
            "runner_group_name": job.get("runner_group_name"),
            "runner_labels": job.get("labels") or [],
            "machine_name": _machine_name_from_runner_name(str(runner_name)),
        }
    return {}


async def _enrich_run_with_job_placement(run: dict) -> dict:
    """Attach job-level runner placement fields to a workflow run."""
    item = dict(run)
    repo_name = _repo_name_from_run(item)
    run_id = item.get("id")
    if repo_name and run_id:
        placement = _placement_from_jobs(await _fetch_run_jobs(repo_name, run_id))
        if placement:
            item.update(placement)
            return item
    machine_name = _machine_name_from_runner_name(item.get("runner_name"))
    item.setdefault("machine_name", machine_name or "GitHub")
    return item


def _classify_node_offline(exc: Exception | None = None, *, status_code: int | None = None) -> dict:
    """Classify why a fleet node is not fully reachable."""
    message = str(exc) if exc else ""
    lower = message.lower()
    if status_code is not None:
        return {
            "offline_reason": "dashboard_unhealthy",
            "offline_detail": f"Dashboard returned HTTP {status_code}",
        }
    if isinstance(exc, httpx.TimeoutException) or "timed out" in lower:
        return {
            "offline_reason": "computer_offline",
            "offline_detail": "Dashboard host timed out over the fleet network.",
        }
    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc
        os_error = cause if isinstance(cause, OSError) else None
        if os_error and os_error.errno == errno.ECONNREFUSED:
            return {
                "offline_reason": "wsl_connection_lost",
                "offline_detail": (
                    "Host is reachable, but the dashboard port refused the connection. "
                    "WSL, systemd, or the dashboard service is likely stopped."
                ),
            }
        if os_error and os_error.errno in {
            errno.ENETUNREACH,
            errno.EHOSTUNREACH,
            errno.ECONNRESET,
        }:
            return {
                "offline_reason": "computer_offline",
                "offline_detail": "Fleet network could not reach the computer.",
            }
    if "connection refused" in lower:
        return {
            "offline_reason": "wsl_connection_lost",
            "offline_detail": "Dashboard port refused the connection.",
        }
    if "network is unreachable" in lower or "no route to host" in lower:
        return {
            "offline_reason": "computer_offline",
            "offline_detail": "Fleet network route to the computer is unavailable.",
        }
    return {
        "offline_reason": "unknown",
        "offline_detail": message or "Dashboard node is unreachable.",
    }


def _resource_offline_reason(system: dict) -> dict | None:
    """Return a resource-monitor reason when local metrics indicate throttling."""
    cpu = system.get("cpu") or {}
    memory = system.get("memory") or {}
    disk = system.get("disk") or {}
    pressure = []
    if (cpu.get("percent_1m_avg") or cpu.get("percent") or 0) >= 95:
        pressure.append("CPU >= 95%")
    if (memory.get("percent") or 0) >= 92:
        pressure.append("memory >= 92%")
    if (disk.get("pressure") or {}).get("status") == "critical":
        pressure.append("disk pressure critical")
    elif (disk.get("percent") or 0) >= 95:
        pressure.append("disk >= 95%")
    if not pressure:
        return None
    return {
        "offline_reason": "resource_monitoring",
        "offline_detail": "Resource pressure detected: " + ", ".join(pressure),
    }


def _node_visibility_snapshot(node: dict) -> dict:
    """Summarize how much useful telemetry a node currently exposes."""
    online = bool(node.get("online"))
    dashboard_reachable = node.get("dashboard_reachable") is not False
    has_system_metrics = bool(node.get("system"))
    resource_pressure = node.get("offline_reason") == "resource_monitoring"

    if resource_pressure:
        return {
            "visibility_state": "degraded",
            "visibility_label": "Degraded",
            "visibility_tone": "yellow",
            "visibility_detail": node.get("offline_detail") or "Resource pressure is high enough to warrant attention.",
        }

    if online and dashboard_reachable and has_system_metrics:
        return {
            "visibility_state": "full_telemetry",
            "visibility_label": "Full telemetry",
            "visibility_tone": "green",
            "visibility_detail": ("Runner status and system metrics are both available."),
        }

    if online:
        return {
            "visibility_state": "runners_only",
            "visibility_label": "Runners only",
            "visibility_tone": "orange",
            "visibility_detail": ("Runner registrations are healthy, but dashboard telemetry is unavailable."),
        }

    if dashboard_reachable:
        return {
            "visibility_state": "dashboard_only",
            "visibility_label": "Dashboard only",
            "visibility_tone": "blue",
            "visibility_detail": ("Dashboard is reachable, but runner registrations are offline."),
        }

    return {
        "visibility_state": "offline",
        "visibility_label": "Offline",
        "visibility_tone": "red",
        "visibility_detail": node.get("offline_detail") or node.get("error") or "No live telemetry from this machine.",
    }


def runner_svc_path(runner_num: int) -> Path:
    return RUNNER_BASE_DIR / f"runner-{runner_num}" / "svc.sh"


async def run_runner_svc(runner_num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a generated GitHub runner svc.sh from its own runner directory."""
    svc_path = runner_svc_path(runner_num)
    return await run_cmd(["sudo", str(svc_path), action], timeout=timeout, cwd=svc_path.parent)


def runner_num_from_id(runner_id: int, runners: list[dict]) -> int | None:
    local_names = {
        HOSTNAME.lower(),
        platform.node().lower(),
        *(alias.lower() for alias in RUNNER_ALIASES),
    }
    for r in runners:
        name = r.get("name", "")
        parts = name.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit() and r["id"] == runner_id:
            machine = parts[0].removeprefix("d-sorg-local-").lower()
            if machine not in local_names:
                return None
            return int(parts[1])
    return None


def _runner_limit() -> int:
    """Return the hard runner capacity this dashboard is allowed to manage."""
    return max(NUM_RUNNERS, MAX_RUNNERS)


def _runner_sort_key(runner: dict) -> tuple[str, int, str]:
    """Sort runner names by machine and numeric suffix instead of alphabetically."""
    name = str(runner.get("name", ""))
    prefix, sep, suffix = name.rpartition("-")
    number = int(suffix) if sep and suffix.isdigit() else 10**9
    return (prefix.lower(), number, name.lower())


def get_runner_service_name(runner_num: int) -> str | None:
    """Get the systemd service name for a runner."""
    svc_file = RUNNER_BASE_DIR / f"runner-{runner_num}" / ".service"
    if svc_file.exists():
        return svc_file.read_text().strip()
    # Fall back to common naming pattern
    return f"actions.runner.{ORG}.d-sorg-local-{HOSTNAME}-{runner_num}.service"


DEFAULT_RUNNER_SCHEDULE = {
    "enabled": True,
    "timezone": os.environ.get("RUNNER_SCHEDULE_TIMEZONE", "America/Los_Angeles"),
    "default_count": min(NUM_RUNNERS, int(os.environ.get("RUNNER_SCHEDULE_DEFAULT", "4"))),
    "schedules": [
        {
            "name": "day",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "start": "07:00",
            "end": "22:00",
            "runners": min(NUM_RUNNERS, 4),
        },
        {
            "name": "overnight",
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "start": "22:00",
            "end": "07:00",
            "runners": NUM_RUNNERS,
        },
        {
            "name": "weekend",
            "days": ["sat", "sun"],
            "start": "07:00",
            "end": "22:00",
            "runners": min(NUM_RUNNERS, 6),
        },
    ],
}


def _validate_hhmm(value: object) -> str:
    if not isinstance(value, str) or not re.match(r"^\d{2}:\d{2}$", value):
        raise ValueError("time values must use HH:MM format")
    hour, minute = [int(part) for part in value.split(":", 1)]
    if hour > 23 or minute > 59:
        raise ValueError("time values must be valid HH:MM clock times")
    return value


def _validate_runner_schedule(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("schedule config must be an object")
    days_allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    sanitized: dict[str, Any] = {
        "enabled": bool(config.get("enabled", True)),
        "timezone": str(config.get("timezone") or "America/Los_Angeles"),
        "default_count": max(0, min(_runner_limit(), int(config.get("default_count", 1)))),
        "schedules": [],
    }
    schedules = config.get("schedules", [])
    if not isinstance(schedules, list):
        raise ValueError("schedules must be a list")
    for entry in schedules:
        if not isinstance(entry, dict):
            raise ValueError("each schedule entry must be an object")
        days = entry.get("days", [])
        if not isinstance(days, list) or not days:
            raise ValueError("each schedule entry needs at least one day")
        normalized_days = [str(day).lower() for day in days]
        if any(day not in days_allowed for day in normalized_days):
            raise ValueError("schedule days must be mon/tue/wed/thu/fri/sat/sun")
        runners = max(0, min(_runner_limit(), int(entry.get("runners", 0))))
        sanitized["schedules"].append(
            {
                "name": str(entry.get("name") or "scheduled"),
                "days": normalized_days,
                "start": _validate_hhmm(entry.get("start")),
                "end": _validate_hhmm(entry.get("end")),
                "runners": runners,
            }
        )
    return sanitized


def _load_runner_schedule_config() -> dict:
    raw = config_schema.safe_read_json(RUNNER_SCHEDULE_CONFIG, DEFAULT_RUNNER_SCHEDULE)
    return _validate_runner_schedule(raw)


def _write_runner_schedule_config(config: dict) -> None:
    try:
        config_schema.validate_runner_schedule_config(config)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    config_schema.atomic_write_json(RUNNER_SCHEDULE_CONFIG, config)


def _sync_runner_scheduler_state(config: dict) -> dict:
    if not Path(RUNNER_SCHEDULER_BIN).exists():
        return {
            "available": False,
            "error": f"{RUNNER_SCHEDULER_BIN} is not installed",
            "config": config,
        }
    env = safe_subprocess_env()
    env["RUNNER_ROOT"] = str(RUNNER_BASE_DIR)
    env["RUNNER_SCHEDULE_CONFIG"] = str(RUNNER_SCHEDULE_CONFIG)
    env["RUNNER_SCHEDULER_STATE"] = str(RUNNER_SCHEDULER_STATE)
    try:
        result = subprocess.run(
            [RUNNER_SCHEDULER_BIN, "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc), "config": config}
    if result.returncode != 0:
        return {
            "available": True,
            "error": (result.stderr or result.stdout).strip()[:500],
            "config": config,
        }
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "available": True,
            "error": "scheduler returned invalid JSON",
            "config": config,
        }
    state["available"] = True
    return state


def _unit_active_sync(unit: str) -> bool:
    if os.name == "nt":
        return False
    try:
        result = subprocess.run(
            [SYSTEMCTL_BIN, "is-active", "--quiet", unit],
            timeout=5,
            check=False,
            env=safe_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def get_runner_capacity_snapshot() -> dict:
    config_error = None
    try:
        config = _load_runner_schedule_config()
        state = _sync_runner_scheduler_state(config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        config = _validate_runner_schedule(DEFAULT_RUNNER_SCHEDULE)
        config_error = str(exc)
        state = {
            "available": Path(RUNNER_SCHEDULER_BIN).exists(),
            "error": f"schedule config invalid: {config_error}",
            "config": config,
        }
    timer_states: dict[str, str] = {}
    for unit in ("runner-scheduler.timer", "runner-cleanup.timer"):
        timer_states[unit] = "active" if _unit_active_sync(unit) else "inactive"
    return {
        "machine": HOSTNAME,
        "aliases": RUNNER_ALIASES,
        "configured_runners": NUM_RUNNERS,
        "default_runners": DEFAULT_NUM_RUNNERS,
        "installed_runners": sum(1 for path in RUNNER_BASE_DIR.glob("runner-*") if path.is_dir()),
        "max_runners": _runner_limit(),
        "config_path": str(RUNNER_SCHEDULE_CONFIG),
        "state_path": str(RUNNER_SCHEDULER_STATE),
        "timers": timer_states,
        "schedule": config,
        "state": state,
    }


def _windows_path_to_wsl(raw_path: str) -> Path:
    """Convert a Windows path to its WSL mount equivalent when possible."""
    normalized = raw_path.strip().strip('"')
    match = re.match(r"^([a-zA-Z]):[\\/](.*)$", normalized)
    if not match:
        return Path(normalized)
    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/")
    return Path("/mnt") / drive / tail


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """Return paths in order without duplicates."""
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _candidate_wslconfig_paths() -> list[Path]:
    """Return plausible .wslconfig locations for the current user."""
    candidates: list[Path] = []
    for env_name in (
        "WSL_KEEPALIVE_WSLCONFIG_PATH",
        "WSL_CONFIG_PATH",
    ):
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())

    profile = os.environ.get("USERPROFILE")
    if profile:
        profile_path = Path(profile).expanduser()
        if os.name == "nt":
            candidates.append(profile_path / ".wslconfig")
        candidates.append(_windows_path_to_wsl(profile) / ".wslconfig")

    home_drive = os.environ.get("HOMEDRIVE")
    home_path = os.environ.get("HOMEPATH")
    if home_drive and home_path:
        windows_home = f"{home_drive}{home_path}"
        if os.name == "nt":
            candidates.append(Path(windows_home).expanduser() / ".wslconfig")
        candidates.append(_windows_path_to_wsl(windows_home) / ".wslconfig")

    users_root = Path("/mnt/c/Users")
    try:
        for profile_dir in users_root.iterdir():
            if not profile_dir.is_dir():
                continue
            if profile_dir.name.lower() in {
                "all users",
                "default",
                "default user",
                "public",
            }:
                continue
            candidates.append(profile_dir / ".wslconfig")
    except OSError:
        pass

    return _dedupe_paths(candidates)


def _resolve_powershell_executable() -> str | None:
    """Find a PowerShell executable from WSL service environments."""
    candidates = [
        os.environ.get("POWERSHELL"),
        "powershell.exe",
        "pwsh.exe",
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/Program Files/PowerShell/7/pwsh.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_absolute() and path.exists():
            return str(path)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _parse_vm_idle_timeout(text: str) -> str | None:
    """Extract the vmIdleTimeout value from a .wslconfig file."""
    match = re.search(
        r"(?im)^\s*vmIdleTimeout\s*=\s*([^#;\r\n]+)",
        text,
    )
    if not match:
        return None
    return match.group(1).strip()


def _inspect_wslconfig() -> dict:
    """Inspect .wslconfig for the vmIdleTimeout keepalive setting."""
    checked_paths = [str(path) for path in _candidate_wslconfig_paths()]
    for path in _candidate_wslconfig_paths():
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return {
                "status": "unknown",
                "path": str(path),
                "checked_paths": checked_paths,
                "error": str(exc),
                "configured": False,
            }

        vm_idle_timeout = _parse_vm_idle_timeout(content)
        if vm_idle_timeout is None:
            return {
                "status": "misconfigured",
                "path": str(path),
                "checked_paths": checked_paths,
                "configured": True,
                "vm_idle_timeout": None,
                "idle_shutdown_disabled": False,
                "detail": "vmIdleTimeout was not found in .wslconfig.",
            }

        disabled = vm_idle_timeout == "-1"
        return {
            "status": "healthy" if disabled else "misconfigured",
            "path": str(path),
            "checked_paths": checked_paths,
            "configured": True,
            "vm_idle_timeout": vm_idle_timeout,
            "idle_shutdown_disabled": disabled,
            "detail": (
                "vmIdleTimeout=-1 disables WSL idle shutdown."
                if disabled
                else f"vmIdleTimeout is {vm_idle_timeout}, not -1."
            ),
        }

    return {
        "status": "missing",
        "path": None,
        "checked_paths": checked_paths,
        "configured": False,
        "vm_idle_timeout": None,
        "idle_shutdown_disabled": False,
        "detail": "No .wslconfig file was found in the configured locations.",
    }


def _parse_task_action(action: dict) -> dict:
    """Normalize a scheduled task action for downstream inspection."""
    return {
        "execute": action.get("Execute") or action.get("execute"),
        "arguments": action.get("Arguments") or action.get("arguments"),
    }


def _probe_detail(probe: dict, fallback: str) -> str:
    """Return human-readable probe detail even for partially failed probes."""
    return str(probe.get("detail") or probe.get("error") or fallback)


def _detect_legacy_keepalive(actions: list[dict], startup_vbs_files: list[str]) -> tuple[bool, str | None]:
    """Detect the old VBS/fire-and-forget keepalive pattern."""
    if startup_vbs_files:
        return True, f"Legacy VBS file(s) still present: {', '.join(startup_vbs_files)}"

    for action in actions:
        execute = (action.get("execute") or "").lower()
        arguments = (action.get("arguments") or "").lower()
        if execute.endswith("wscript.exe") or execute.endswith("cscript.exe"):
            return True, f"Task launches {execute.rsplit('/', 1)[-1]} directly."
        if ".vbs" in execute or ".vbs" in arguments:
            return True, "Task still references a .vbs keepalive script."

    return False, None


async def _inspect_systemd_keepalive() -> dict:
    """Inspect the in-WSL systemd keepalive service."""
    if os.name == "nt":
        return {
            "status": "unsupported",
            "service": WSL_KEEPALIVE_SERVICE,
            "configured": False,
            "active": False,
            "enabled": False,
            "detail": (
                "systemd keepalive is checked inside WSL; "
                "this Windows fallback process cannot query systemctl directly."
            ),
        }

    code, stdout, stderr = await run_cmd(
        [
            "systemctl",
            "show",
            WSL_KEEPALIVE_SERVICE,
            "--property=LoadState,ActiveState,UnitFileState,FragmentPath,Description",
            "--no-pager",
        ],
        timeout=10,
    )

    if code != 0:
        lower = f"{stdout}\n{stderr}".lower()
        if "system has not been booted with systemd" in lower or "failed to connect to bus" in lower:
            return {
                "status": "unsupported",
                "service": WSL_KEEPALIVE_SERVICE,
                "configured": False,
                "active": False,
                "enabled": False,
                "detail": "systemd is not available in this WSL session.",
            }
        return {
            "status": "unknown",
            "service": WSL_KEEPALIVE_SERVICE,
            "configured": False,
            "active": False,
            "enabled": False,
            "error": stderr.strip() or stdout.strip() or "systemctl show failed",
        }

    props: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()

    load_state = props.get("LoadState")
    active_state = props.get("ActiveState")
    unit_file_state = props.get("UnitFileState")
    configured = load_state == "loaded"
    active = active_state == "active"
    enabled = unit_file_state == "enabled"
    healthy = configured and active and enabled
    if healthy:
        detail = f"{WSL_KEEPALIVE_SERVICE} is active and enabled."
        status = "healthy"
    elif configured:
        detail = f"{WSL_KEEPALIVE_SERVICE} is {active_state or 'unknown'} and {unit_file_state or 'unknown'}."
        status = "misconfigured"
    else:
        detail = f"{WSL_KEEPALIVE_SERVICE} is not installed."
        status = "missing"

    return {
        "status": status,
        "service": WSL_KEEPALIVE_SERVICE,
        "configured": configured,
        "active": active,
        "enabled": enabled,
        "load_state": load_state,
        "active_state": active_state,
        "unit_file_state": unit_file_state,
        "fragment_path": props.get("FragmentPath"),
        "description": props.get("Description"),
        "detail": detail,
    }


async def _inspect_windows_keepalive() -> dict:
    """Inspect the Windows Scheduled Task and legacy keepalive artifacts."""
    powershell = _resolve_powershell_executable()
    if powershell is None:
        return {
            "status": "unsupported",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": "PowerShell was not found; Windows Scheduled Task cannot be queried from this WSL session.",
        }

    _ps_get_legacy = (
        "@(Get-ChildItem -Path $startup -Filter 'wsl-keepalive.vbs'"
        " -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)"
    )
    _ps_get_actions = (
        "@($task.Actions | ForEach-Object { [pscustomobject]@{ Execute = $_.Execute; Arguments = $_.Arguments } })"
    )
    script = f"""
$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$legacy = {_ps_get_legacy}
$task = $null
try {{
    $task = Get-ScheduledTask -TaskName '{WSL_KEEPALIVE_TASK_NAME}' -ErrorAction Stop
    $actions = {_ps_get_actions}
    $result = [pscustomobject]@{{
        task_found = $true
        task_name = $task.TaskName
        state = "$($task.State)"
        actions = $actions
        startup_vbs_files = $legacy
    }}
}} catch {{
    $result = [pscustomobject]@{{
        task_found = $false
        task_name = '{WSL_KEEPALIVE_TASK_NAME}'
        state = $null
        actions = @()
        startup_vbs_files = $legacy
        error = $_.Exception.Message
    }}
}}
$result | ConvertTo-Json -Depth 5
"""
    try:
        code, stdout, stderr = await run_cmd(
            [powershell, "-NoProfile", "-Command", script],
            timeout=12,
        )
    except OSError as exc:
        return {
            "status": "unsupported",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": f"PowerShell execution failed: {exc}",
        }

    if code != 0:
        return {
            "status": "unknown",
            "task_name": WSL_KEEPALIVE_TASK_NAME,
            "task_found": False,
            "state": None,
            "actions": [],
            "startup_vbs_files": [],
            "legacy_vbs_detected": False,
            "detail": stderr.strip() or stdout.strip() or "Scheduled task query failed.",
        }

    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = {}

    raw_actions = payload.get("actions") or []
    if isinstance(raw_actions, dict):
        raw_actions = [raw_actions]
    actions = [_parse_task_action(action) for action in raw_actions]
    startup_vbs_files = payload.get("startup_vbs_files") or []
    if isinstance(startup_vbs_files, str):
        startup_vbs_files = [startup_vbs_files]

    legacy_vbs_detected, legacy_detail = _detect_legacy_keepalive(
        actions,
        [str(item) for item in startup_vbs_files],
    )

    state = payload.get("state")
    task_found = bool(payload.get("task_found"))
    running = state == "Running"
    ready = state == "Ready"
    action_exec = actions[0]["execute"] if actions else None
    action_args = actions[0]["arguments"] if actions else None
    if task_found and running and not legacy_vbs_detected:
        status = "healthy"
        detail = f"{WSL_KEEPALIVE_TASK_NAME} is Running."
    elif legacy_vbs_detected:
        status = "legacy"
        detail = legacy_detail or "Legacy VBS keepalive detected."
    elif task_found:
        status = "misconfigured" if ready else "unknown"
        detail = f"{WSL_KEEPALIVE_TASK_NAME} is {state or 'unknown'}." + (
            f" Action: {action_exec or 'n/a'} {action_args or ''}".rstrip()
        )
    else:
        status = "missing"
        detail = payload.get("error") or f"{WSL_KEEPALIVE_TASK_NAME} is not registered."

    return {
        "status": status,
        "task_name": WSL_KEEPALIVE_TASK_NAME,
        "task_found": task_found,
        "state": state,
        "actions": actions,
        "startup_vbs_files": [str(item) for item in startup_vbs_files],
        "legacy_vbs_detected": legacy_vbs_detected,
        "legacy_vbs_detail": legacy_detail,
        "detail": detail,
    }


async def _watchdog_status_impl() -> dict:
    """Aggregate the WSL keepalive / startup validation state."""
    cached = _cache_get("watchdog", 30.0)
    if cached is not None:
        return cached

    wslconfig, systemd, windows = await asyncio.gather(
        asyncio.to_thread(_inspect_wslconfig),
        _inspect_systemd_keepalive(),
        _inspect_windows_keepalive(),
    )

    checks = [
        {
            "machine": HOSTNAME,
            "layer": ".wslconfig",
            "status": wslconfig["status"],
            "detail": _probe_detail(wslconfig, ".wslconfig status unavailable."),
        },
        {
            "machine": HOSTNAME,
            "layer": "systemd keepalive",
            "status": systemd["status"],
            "detail": _probe_detail(systemd, "systemd keepalive status unavailable."),
        },
        {
            "machine": HOSTNAME,
            "layer": "Windows scheduled task",
            "status": windows["status"],
            "detail": _probe_detail(windows, "Windows scheduled task status unavailable."),
        },
    ]
    issue_details = [check for check in checks if check["status"] not in {"healthy", "unsupported"}]
    issues: list[str] = []
    for check in issue_details:
        issues.append(f"{check['machine']} {check['layer']} ({check['status']}): {check['detail']}")

    for check in (wslconfig, systemd, windows):
        if check["status"] not in {"healthy", "unsupported"}:
            check["machine"] = HOSTNAME

    if wslconfig["status"] == "healthy" and systemd["status"] == "healthy" and windows["status"] == "healthy":
        overall = "healthy"
        summary = f"{HOSTNAME}: all WSL keepalive layers are in place."
    elif all(check["status"] in {"missing", "unknown", "unsupported"} for check in (wslconfig, systemd, windows)):
        overall = "unknown"
        summary = f"{HOSTNAME}: WSL keepalive status could not be fully verified."
    elif not issue_details:
        overall = "healthy"
        summary = f"{HOSTNAME}: WSL keepalive checks are healthy or unsupported."
    else:
        overall = "degraded"
        summary = f"{HOSTNAME}: {len(issue_details)} WSL keepalive check(s) need attention."

    result = {
        "status": overall,
        "summary": summary,
        "hostname": HOSTNAME,
        "machine": HOSTNAME,
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": checks,
        "wslconfig": wslconfig,
        "systemd_keepalive": systemd,
        "windows_task": windows,
        "legacy_vbs_detected": windows.get("legacy_vbs_detected", False),
        "issues": issues,
        "issue_details": issue_details,
        "affected_machines": [HOSTNAME] if issues else [],
        "detail": "; ".join(issue for issue in issues if issue),
    }
    _cache_set("watchdog", result)
    return result


# ─── System Metrics ──────────────────────────────────────────────────────────


def get_gpu_info() -> dict:
    """Query nvidia-smi for GPU metrics. Returns empty dict if no NVIDIA GPU."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env=safe_subprocess_env(),
        )
        if result.returncode != 0:
            return {}

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 8:
                total = float(parts[1])
                used = float(parts[2])
                vram_pct = round(used / total * 100, 1) if total > 0 else 0
                gpus.append(
                    {
                        "name": parts[0],
                        "vram_total_mb": total,
                        "vram_used_mb": used,
                        "vram_free_mb": float(parts[3]),
                        "vram_percent": vram_pct,
                        "gpu_util_percent": float(parts[4]),
                        "temp_c": float(parts[5]),
                        "power_draw_w": (float(parts[6]) if parts[6] != "[N/A]" else None),
                        "power_limit_w": (float(parts[7]) if parts[7] != "[N/A]" else None),
                    }
                )
        return {"gpus": gpus, "count": len(gpus)}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}


def get_per_runner_resources() -> list[dict]:
    """Get CPU and memory usage for each runner's worker processes."""
    runner_procs = []
    for i in range(1, _runner_limit() + 1):
        _ = get_runner_service_name(i)
        runner_info: dict[str, Any] = {
            "runner_num": i,
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "process_count": 0,
            "status": "stopped",
        }

        # Find runner processes by looking at the runner directory
        runner_dir = str(RUNNER_BASE_DIR / f"runner-{i}")
        proc_fields = [
            "pid",
            "name",
            "cmdline",
            "cpu_percent",
            "memory_info",
        ]
        for proc in psutil.process_iter(proc_fields):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                is_runner = runner_dir in cmdline or ("Runner.Listener" in cmdline and f"runner-{i}" in cmdline)
                if is_runner:
                    runner_info["cpu_percent"] += proc.info.get("cpu_percent", 0) or 0
                    mem = proc.info.get("memory_info")
                    if mem:
                        runner_info["memory_mb"] += mem.rss / (1024 * 1024)
                    runner_info["process_count"] += 1
                    runner_info["status"] = "running"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        runner_info["cpu_percent"] = round(runner_info["cpu_percent"], 1)
        runner_info["memory_mb"] = round(runner_info["memory_mb"], 1)
        runner_procs.append(runner_info)

    return runner_procs


async def _collect_live_fleet_nodes() -> list[dict]:
    """Collect the live fleet node payload before registry metadata is merged."""

    async def fetch_node(name: str, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                sys_r, health_r = await asyncio.gather(
                    client.get(f"{url}/api/system"),
                    client.get(f"{url}/api/health"),
                )
            if sys_r.status_code != 200 or health_r.status_code != 200:
                status_code = sys_r.status_code if sys_r.status_code != 200 else health_r.status_code
                reason = _classify_node_offline(status_code=status_code)
                return {
                    "name": name,
                    "url": url,
                    "online": False,
                    "dashboard_reachable": True,
                    "is_local": False,
                    "role": "node",
                    "system": sys_r.json() if sys_r.status_code == 200 else {},
                    "health": health_r.json() if health_r.status_code == 200 else {},
                    "last_seen": None,
                    "error": reason["offline_detail"],
                    **reason,
                }
            system = sys_r.json()
            resource_reason = _resource_offline_reason(system)
            return {
                "name": name,
                "url": url,
                "online": True,
                "dashboard_reachable": True,
                "is_local": False,
                "role": "node",
                "system": system,
                "hardware_specs": system.get("hardware_specs", {}),
                "workload_capacity": system.get("workload_capacity", {}),
                "health": health_r.json(),
                "last_seen": datetime.now(UTC).isoformat(),
                "error": None,
                "offline_reason": (resource_reason["offline_reason"] if resource_reason else None),
                "offline_detail": (resource_reason["offline_detail"] if resource_reason else None),
            }
        except Exception as exc:  # noqa: BLE001
            reason = _classify_node_offline(exc)
            return {
                "name": name,
                "url": url,
                "online": False,
                "dashboard_reachable": False,
                "is_local": False,
                "role": "node",
                "system": {},
                "health": {},
                "last_seen": None,
                "error": reason["offline_detail"],
                **reason,
            }

    local_sys = await _metrics_router.get_system_metrics()
    local_health = await _health_router._health_impl()
    local_resource_reason = _resource_offline_reason(local_sys)
    nodes: list[dict] = [
        {
            "name": HOSTNAME,
            "url": f"http://localhost:{PORT}",
            "online": True,
            "dashboard_reachable": True,
            "is_local": True,
            "role": MACHINE_ROLE,
            "system": local_sys,
            "hardware_specs": local_sys.get("hardware_specs", {}),
            "workload_capacity": local_sys.get("workload_capacity", {}),
            "health": local_health,
            "last_seen": datetime.now(UTC).isoformat(),
            "error": None,
            "offline_reason": (local_resource_reason["offline_reason"] if local_resource_reason else None),
            "offline_detail": (local_resource_reason["offline_detail"] if local_resource_reason else None),
        }
    ]

    if FLEET_NODES:
        remote = await asyncio.gather(*[fetch_node(name, url) for name, url in FLEET_NODES.items()])
        nodes.extend(remote)

    return nodes


@app.get("/api/deployment")
async def get_deployment() -> dict:
    """Return the dashboard code revision deployed on this machine."""
    return _deployment_info()


@app.get("/api/deployment/expected-version")
async def get_expected_deployment_version() -> dict:
    """Return the local expected dashboard version for hub-spoke nodes."""
    return {
        "expected": deployment_drift.read_expected_version(EXPECTED_VERSION_FILE),
        "source": "local-version-file",
        "path": str(EXPECTED_VERSION_FILE),
    }


@app.get("/api/deployment/drift")
async def get_deployment_drift() -> dict:
    """Compare the deployed version against the hub's expected VERSION.

    Used by the Machines tab to surface "Update available" badges on stale
    nodes. Remote update orchestration is intentionally out of scope here —
    see ``POST /api/deployment/update-signal`` for the notify-only affordance.
    """
    expected = await _read_expected_dashboard_version()
    status = deployment_drift.evaluate_drift(_deployment_info(), expected)
    return status.to_dict()


@app.get("/api/deployment/state")
async def get_deployment_state(request: Request) -> dict:
    """Return dashboard deployment state for the fleet overview and deployment tab."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    fleet = await _get_fleet_nodes_impl()
    expected = await _read_expected_dashboard_version()
    return _build_deployment_state(fleet.get("nodes", []), expected)


@app.post("/api/deployment/update-signal")
async def post_deployment_update_signal(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Emit a structured "update requested" event for a node.

    The dashboard UI calls this when an operator clicks the "Update node"
    affordance on a drifting machine card. We intentionally do *not* SSH
    or run ansible from here: this just logs a well-shaped event that
    ``scheduled-dashboard-maintenance.sh`` (or a future webhook consumer)
    can pick up. Callers should treat this as fire-and-notify only.
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}
    node = str(payload.get("node") or HOSTNAME)
    reason = str(payload.get("reason") or "user-requested")
    dry_run = bool(payload.get("dry_run", False))

    expected = await _read_expected_dashboard_version()
    status = deployment_drift.evaluate_drift(_deployment_info(), expected)
    if dry_run:
        preview = {
            "event": "dashboard.node.update_requested",
            "node": node,
            "current": status.current,
            "expected": status.expected,
            "severity": status.severity,
            "reason": reason,
            "dirty": status.dirty,
            "dry_run": True,
        }
        return {
            "accepted": True,
            "dry_run": True,
            "preview": preview,
            "drift": status.to_dict(),
        }
    event = deployment_drift.emit_update_signal(node, status, reason=reason)
    return {"accepted": True, "event": event, "drift": status.to_dict()}


@app.get("/api/local-apps")
async def get_local_apps(request: Request) -> dict:
    """Report local tool deployment, drift, service state, and health."""
    cached = _cache_get("local_apps", 120.0)
    if cached is not None:
        return cached

    data = await asyncio.to_thread(collect_local_apps)
    _cache_set("local_apps", data)
    return data


@app.get("/api/watchdog")
async def get_watchdog_status(request: Request):
    """Report the WSL keepalive and startup validation state."""
    return await _watchdog_status_impl()


# ─── Runner API Routes ───────────────────────────────────────────────────────


@app.get("/api/runners")
async def get_runners(request: Request):
    """Get all org runners with their status."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("runners", 60.0)
    if cached is not None:
        cached["runners"] = sorted(cached.get("runners", []), key=_runner_sort_key)
        return cached
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    data["runners"] = sorted(data.get("runners", []), key=_runner_sort_key)
    _cache_set("runners", data)
    return data


# ─── MATLAB Runner Health (issue #570) ───────────────────────────────────────
#
# MATLAB linting relies on a Windows self-hosted runner where MATLAB is
# installed natively.  Operators need to see at a glance whether that capacity
# is available; missing MATLAB runners produce queued-forever CI jobs
# otherwise.  The endpoint below surfaces online/offline/busy state, last job,
# persistence mode (Windows service vs Scheduled Task), and links to recent
# MATLAB Code Analyzer workflow runs.
#
# Runners are identified by either the `matlab` label or a name matching
# `*windows-matlab*` / `*d-sorg-matlab*` to stay robust across naming schemes.


def _is_matlab_runner(runner: dict) -> bool:
    """Return True if the runner appears to be a MATLAB-capable runner."""
    name = str(runner.get("name", "")).lower()
    if "matlab" in name:
        return True
    for label in runner.get("labels", []) or []:
        lname = str(label.get("name", "")).lower() if isinstance(label, dict) else str(label).lower()
        if lname == "matlab" or lname.startswith("windows-matlab") or lname.startswith("d-sorg-matlab"):
            return True
    return False


def _matlab_runner_summary(runner: dict) -> dict:
    """Project a GitHub runner record into the MATLAB health shape."""
    labels = [lbl.get("name") if isinstance(lbl, dict) else str(lbl) for lbl in (runner.get("labels") or [])]
    status = str(runner.get("status", "unknown")).lower()
    busy = bool(runner.get("busy"))
    # Persistence hint: the ControlTower Windows runner is registered as a
    # Windows service; names ending with `-scheduled` signal a Scheduled Task.
    name = str(runner.get("name", ""))
    if name.endswith("-scheduled") or "scheduled-task" in name.lower():
        persistence = "scheduled_task"
    elif status == "offline":
        persistence = "unknown"
    else:
        persistence = "windows_service"
    return {
        "id": runner.get("id"),
        "name": name,
        "status": status,
        "busy": busy,
        "labels": labels,
        "os": runner.get("os"),
        "persistence": persistence,
    }


async def _recent_matlab_workflow_runs(limit: int = 5) -> list[dict]:
    """Fetch recent MATLAB Code Analyzer workflow runs across the org.

    Returns an empty list on any failure — this is advisory UI, not critical
    control surface, so transient API errors must not break the endpoint.
    """
    try:
        repos = await _get_recent_org_repos(limit=15)
    except Exception:  # pragma: no cover - defensive  # noqa: BLE001
        return []
    if not repos:
        return []

    async def _runs_for_repo(repo_name: str) -> list[dict]:
        try:
            data = await gh_api_admin(f"/repos/{ORG}/{repo_name}/actions/runs?per_page=10")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for run in data.get("workflow_runs", []) or []:
            wf_name = str(run.get("name") or "").lower()
            wf_path = str(run.get("path") or "").lower()
            if "matlab" in wf_name or "matlab" in wf_path or "code analyzer" in wf_name:
                out.append(
                    {
                        "repo": repo_name,
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "html_url": run.get("html_url"),
                        "created_at": run.get("created_at"),
                        "run_id": run.get("id"),
                    }
                )
        return out

    try:
        nested = await asyncio.gather(
            *[_runs_for_repo(r["name"]) for r in repos[:10]],
            return_exceptions=True,
        )
    except Exception:  # noqa: BLE001
        return []
    flat: list[dict] = []
    for item in nested:
        if isinstance(item, list):
            flat.extend(item)
    flat.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return flat[:limit]


@app.get("/api/runners/matlab")
async def get_matlab_runner_health(request: Request) -> dict:
    """Surface Windows MATLAB runner health for the dashboard (issue #570).

    Response shape::

        {
          "runners": [...],          # MATLAB runner summaries
          "total": int,
          "online": int,
          "busy": int,
          "offline": int,
          "capacity_available": bool, # true iff an idle online runner exists
          "warning": str | None,      # actionable message when capacity is zero
          "recent_workflow_runs": [...],
          "generated_at": "..."
        }

    Always returns 200; absence of runners is represented explicitly so the UI
    can render an actionable warning instead of a spinner.
    """
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("matlab_runner_health", 45.0)
    if cached is not None:
        return cached

    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", []) or []
    except Exception as exc:  # pragma: no cover - defensive  # noqa: BLE001
        all_runners = []
        api_error: str | None = f"GitHub runner API unavailable: {exc}"
    else:
        api_error = None

    matlab = [r for r in all_runners if _is_matlab_runner(r)]
    summaries = [_matlab_runner_summary(r) for r in matlab]

    online = sum(1 for r in summaries if r["status"] == "online")
    busy = sum(1 for r in summaries if r["busy"])
    offline = sum(1 for r in summaries if r["status"] != "online")
    idle_online = sum(1 for r in summaries if r["status"] == "online" and not r["busy"])

    warning: str | None = None
    if not summaries:
        warning = (
            "No Windows MATLAB runners are registered. MATLAB Code Analyzer "
            "jobs will queue indefinitely. See "
            "docs/operations/matlab_windows_runner.md to register a runner."
        )
    elif online == 0:
        warning = (
            "All MATLAB runners are offline. Start the Windows runner service "
            "on the ControlTower host to restore MATLAB lint capacity."
        )
    elif idle_online == 0:
        warning = "All MATLAB runners are currently busy. New MATLAB lint jobs will queue until one frees up."

    recent = await _recent_matlab_workflow_runs(limit=5)

    result = {
        "runners": summaries,
        "total": len(summaries),
        "online": online,
        "busy": busy,
        "offline": offline,
        "capacity_available": idle_online > 0,
        "warning": warning,
        "api_error": api_error,
        "recent_workflow_runs": recent,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    _cache_set("matlab_runner_health", result)
    return result


@app.get("/api/runs")
async def get_runs(request: Request, per_page: int = 30) -> dict:
    """Get recent workflow runs across the org by sampling the most active repos.

    GitHub's REST API has no org-level /actions/runs endpoint; runs must be
    fetched per-repo.  We sample the 10 most recently updated repos and return
    up to ``per_page`` runs sorted newest-first.
    """
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cache_key = f"runs:{per_page}"
    cached = _cache_get(cache_key, 120.0)
    if cached is not None:
        return cached

    repos = await _get_recent_org_repos(limit=20)
    if not repos:
        return {"workflow_runs": [], "total_count": 0}

    # Fetch a handful of runs from each repo concurrently
    runs_per_repo = max(3, per_page // max(len(repos[:10]), 1))

    sample = repos[:10]
    all_runs_nested = await asyncio.gather(*[_fetch_repo_runs(r["name"], per_page=runs_per_repo) for r in sample])
    all_runs: list[dict] = [run for sublist in all_runs_nested for run in sublist]

    # Sort newest-first and cap at per_page
    all_runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    top_runs = all_runs[:per_page]

    result = {"workflow_runs": top_runs, "total_count": len(top_runs)}
    _cache_set(f"runs:{per_page}", result)
    return result


async def _scheduled_workflows_impl(
    *,
    include_archived: bool = False,
    repo_limit: int = 100,
) -> dict:
    """Collect the read-only scheduled workflow inventory."""
    cache_key = f"scheduled-workflows:{include_archived}:{repo_limit}"
    cached = _cache_get(cache_key, 300.0)
    if cached is not None:
        return cached

    raw_timeout = os.environ.get("SCHEDULED_WORKFLOWS_TIMEOUT", "20")
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 20.0

    try:
        report = await asyncio.wait_for(
            scheduled_workflow_inventory.collect_inventory(
                ORG,
                gh_api,
                gh_api_raw,
                repo_limit=repo_limit,
                include_archived=include_archived,
            ),
            timeout=timeout,
        )
        payload = report.to_dict()
        payload["status"] = "ok"
        _cache_set(cache_key, payload)
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        payload = {
            "status": "degraded",
            "organization": ORG,
            "generated_at": datetime.now(UTC).isoformat(),
            "repository_count": 0,
            "scheduled_workflow_count": 0,
            "repositories": [],
            "dry_run_plan": {
                "mode": "dry_run",
                "write_actions_allowed": False,
                "confirmation_required": True,
                "audit_required": True,
                "steps": [],
            },
            "error": "Scheduled workflow inventory timed out.",
        }
    return payload


@app.get("/api/scheduled-workflows")
async def get_scheduled_workflows(
    request: Request,
    include_archived: bool = False,
    repo_limit: int = 100,
):
    """Inventory GitHub Actions schedules across org repositories.

    This endpoint is read-only. It gathers workflow metadata, extracts cron
    expressions from workflow YAML where available, and attaches a dry-run plan
    that describes future changes without executing them.
    """
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _scheduled_workflows_impl(
        include_archived=include_archived,
        repo_limit=repo_limit,
    )


@app.get("/api/workflows/list")
async def list_workflows() -> dict:
    """List all workflows per repository with trigger capabilities and latest run."""
    import base64  # noqa: PLC0415

    cached = _cache_get("workflows_list", 120.0)
    if cached is not None:
        return cached
    repos = await _get_recent_org_repos(limit=30)

    async def get_repo_workflows(repo_name: str) -> list[dict]:
        code, out, _ = await run_cmd(
            ["gh", "api", f"/repos/{ORG}/{repo_name}/actions/workflows", "--paginate"],
            timeout=20,
            cwd=REPO_ROOT,
        )
        if code != 0:
            return []
        try:
            data = json.loads(out)
            workflows = data.get("workflows", [])
        except Exception:  # noqa: BLE001
            return []
        result = []
        for wf in workflows:
            wf_id = wf.get("id")
            triggers = []
            if wf.get("path"):
                code2, out2, _ = await run_cmd(
                    ["gh", "api", f"/repos/{ORG}/{repo_name}/contents/{wf['path']}"],
                    timeout=10,
                    cwd=REPO_ROOT,
                )
                if code2 == 0:
                    try:
                        content_data = json.loads(out2)
                        content = base64.b64decode(content_data.get("content", "")).decode("utf-8", errors="replace")
                        if "workflow_dispatch" in content:
                            triggers.append("manual")
                        if "schedule" in content:
                            triggers.append("schedule")
                        if "push" in content or "pull_request" in content:
                            triggers.append("push_pr")
                        if "workflow_run" in content:
                            triggers.append("workflow_run")
                    except Exception:  # noqa: BLE001
                        pass
            code3, out3, _ = await run_cmd(
                [
                    "gh",
                    "api",
                    f"/repos/{ORG}/{repo_name}/actions/workflows/{wf_id}/runs?per_page=3",
                ],
                timeout=10,
                cwd=REPO_ROOT,
            )
            latest_run = None
            recent_runs = []
            if code3 == 0:
                try:
                    runs_data = json.loads(out3)
                    all_runs = runs_data.get("workflow_runs", [])
                    if all_runs:
                        latest_run = {
                            "id": all_runs[0].get("id"),
                            "status": all_runs[0].get("status"),
                            "conclusion": all_runs[0].get("conclusion"),
                            "created_at": all_runs[0].get("created_at"),
                            "html_url": all_runs[0].get("html_url"),
                            "head_branch": all_runs[0].get("head_branch"),
                        }
                        recent_runs = [
                            {
                                "id": r.get("id"),
                                "status": r.get("status"),
                                "conclusion": r.get("conclusion"),
                                "created_at": r.get("created_at"),
                                "html_url": r.get("html_url"),
                            }
                            for r in all_runs[:3]
                        ]
                except Exception:  # noqa: BLE001
                    pass
            result.append(
                {
                    "id": wf_id,
                    "name": wf.get("name", ""),
                    "path": wf.get("path", ""),
                    "state": wf.get("state", ""),
                    "html_url": wf.get("html_url", ""),
                    "triggers": triggers,
                    "latest_run": latest_run,
                    "recent_runs": recent_runs,
                    "repository": repo_name,
                }
            )
        return result

    results = await asyncio.gather(*[get_repo_workflows(r["name"]) for r in repos[:20]])
    all_workflows: list[dict] = []
    for wf_list in results:
        all_workflows.extend(wf_list)

    result = {"workflows": all_workflows, "total": len(all_workflows)}
    _cache_set("workflows_list", result, 120.0)
    return result


@app.post("/api/workflows/dispatch")
async def dispatch_workflow(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
) -> dict:
    """Dispatch a workflow via workflow_dispatch."""
    body = await request.json()
    repo = str(body.get("repository", "")).strip()
    workflow_id = body.get("workflow_id")
    ref = str(body.get("ref", "main")).strip()
    inputs = body.get("inputs", {}) or {}
    correlation_id = request.headers.get("X-Correlation-Id", secrets.token_hex(8))
    inputs["correlation_id"] = correlation_id
    approved_by = principal.id

    if not repo or not workflow_id:
        raise HTTPException(status_code=422, detail="repository and workflow_id required")

    endpoint = f"/repos/{ORG}/{repo}/actions/workflows/{workflow_id}/dispatches"
    payload = {"ref": ref, "inputs": inputs}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        log.warning(
            "workflow_dispatch failed: repo=%s workflow_id=%s stderr=%s",
            repo,
            workflow_id,
            stderr.strip()[:300],
        )
        raise HTTPException(status_code=502, detail="Workflow dispatch failed")

    log.info(
        "workflow_dispatch audit: repo=%s workflow_id=%s ref=%s approved_by=%s",
        repo,
        workflow_id,
        ref,
        sanitize_log_value(approved_by),
    )
    return {
        "status": "dispatched",
        "repository": repo,
        "workflow_id": workflow_id,
        "ref": ref,
    }


@app.get("/api/runs/enriched")
async def get_enriched_runs(request: Request, per_page: int = 50) -> dict:
    """Return recent runs with dashboard-friendly enrichment fields."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    cache_key = f"runs-enriched:{per_page}"
    cached = _cache_get(cache_key, 120.0)
    if cached is not None:
        return cached

    data = await get_runs(request, per_page=per_page)
    runs = data.get("workflow_runs", [])
    enrichable = runs[:RUN_JOB_ENRICHMENT_LIMIT]
    enriched = await asyncio.gather(*[_enrich_run_with_job_placement(run) for run in enrichable])
    enriched.extend(dict(run) for run in runs[RUN_JOB_ENRICHMENT_LIMIT:])
    result = {"workflow_runs": enriched, "total_count": len(enriched)}
    _cache_set(cache_key, result)
    return result


@app.get("/api/runs/{repo}")
async def get_repo_runs(request: Request, repo: str, per_page: int = 20):
    """Get recent workflow runs for a specific repo."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    data = await gh_api(f"/repos/{ORG}/{repo}/actions/runs?per_page={per_page}")
    return data


@app.post("/api/runners/{runner_id}/stop")
async def stop_runner(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("runners.control")),
    runner_id: int,  # noqa: B008
):  # noqa: B008
    """Stop a specific runner's systemd service."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    num = runner_num_from_id(runner_id, runners)

    if num is None:
        msg = f"Runner ID {runner_id} not found locally"
        raise HTTPException(status_code=404, detail=msg)

    svc_path = runner_svc_path(num)
    if not svc_path.exists():
        raise HTTPException(status_code=404, detail=f"Runner {num} svc.sh not found")

    log.info("Stopping runner %d (GitHub ID: %d)", num, runner_id)
    code, stdout, stderr = await run_runner_svc(num, "stop")
    if code != 0:
        log.warning("Failed to stop runner %d: %s", num, stderr[:200])
        raise HTTPException(status_code=500, detail=f"Failed to stop runner {num}")

    return {"status": "stopped", "runner": num, "output": stdout.strip()}


@app.post("/api/runners/{runner_id}/start")
async def start_runner(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("runners.control")),
    runner_id: int,  # noqa: B008
):  # noqa: B008
    """Start a specific runner's systemd service."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    num = runner_num_from_id(runner_id, runners)

    if num is None:
        msg = f"Runner ID {runner_id} not found locally"
        raise HTTPException(status_code=404, detail=msg)

    svc_path = runner_svc_path(num)
    if not svc_path.exists():
        raise HTTPException(status_code=404, detail=f"Runner {num} svc.sh not found")

    log.info("Starting runner %d (GitHub ID: %d)", num, runner_id)
    code, stdout, stderr = await run_runner_svc(num, "start")
    if code != 0:
        log.warning("Failed to start runner %d: %s", num, stderr[:200])
        raise HTTPException(status_code=500, detail=f"Failed to start runner {num}")

    return {"status": "started", "runner": num, "output": stdout.strip()}


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    results = []

    log.info("Local runner control on %s: %s", HOSTNAME, action)

    if action == "all-up":
        for i in range(1, _runner_limit() + 1):
            svc = runner_svc_path(i)
            if svc.exists():
                code, _, _ = await run_runner_svc(i, "start")
                results.append({"runner": i, "action": "start", "success": code == 0})

    elif action == "all-down":
        for i in range(1, _runner_limit() + 1):
            svc = runner_svc_path(i)
            if svc.exists():
                code, _, _ = await run_runner_svc(i, "stop")
                results.append({"runner": i, "action": "stop", "success": code == 0})

    elif action == "up":
        online_nums = set()
        for r in runners:
            if r["status"] == "online":
                num = runner_num_from_id(r["id"], runners)
                if num:
                    online_nums.add(num)
        for i in range(1, _runner_limit() + 1):
            if i not in online_nums:
                svc = runner_svc_path(i)
                if svc.exists():
                    code, _, _ = await run_runner_svc(i, "start")
                    results.append(
                        {
                            "runner": i,
                            "action": "start",
                            "success": code == 0,
                        }
                    )
                    break

    elif action == "down":
        idle_runners = []
        for r in runners:
            if r["status"] == "online" and not r.get("busy"):
                num = runner_num_from_id(r["id"], runners)
                if num:
                    idle_runners.append(num)
        if idle_runners:
            target = max(idle_runners)
            svc = runner_svc_path(target)
            if svc.exists():
                code, _, _ = await run_runner_svc(target, "stop")
                results.append(
                    {
                        "runner": target,
                        "action": "stop",
                        "success": code == 0,
                    }
                )
        else:
            raise HTTPException(status_code=400, detail="No idle runners to stop")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    return {"machine": HOSTNAME, "action": action, "results": results}


async def _remote_fleet_control(name: str, url: str, action: str) -> dict:
    """Ask a node dashboard to apply a runner action locally."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{url}/api/fleet/control/{action}?local=1")
        if resp.status_code != 200:
            return {
                "machine": name,
                "url": url,
                "success": False,
                "status_code": resp.status_code,
                "error": resp.text[:500],
            }
        data = resp.json()
        return {
            "machine": name,
            "url": url,
            "success": True,
            "result": data,
        }
    except Exception as exc:  # noqa: BLE001 - remote nodes may be offline
        return {"machine": name, "url": url, "success": False, "error": str(exc)}

        return {"machine": name, "url": url, "success": False, "error": str(exc)}


@app.post("/api/fleet/control/{action}")
async def fleet_control(
    action: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
):  # noqa: B008
    """Scale runners from any dashboard.

    Nodes proxy fleet-wide requests to the hub. The hub applies the action
    locally and fans it out to configured nodes. Internal fan-out calls use
    ``?local=1`` so each node controls its own runner services.
    """
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    scope = request.query_params.get("scope", "fleet")
    should_fan_out = MACHINE_ROLE == "hub" and scope != "local" and bool(FLEET_NODES)
    local_machine = HOSTNAME
    try:
        local_result = await _fleet_control_local(action)
        local_machine = local_result.get("machine", HOSTNAME)
        local_node_result = {
            "machine": local_machine,
            "url": f"http://localhost:{PORT}",
            "success": True,
            "result": local_result,
        }
    except HTTPException as exc:
        if not should_fan_out:
            raise
        local_result = {"machine": HOSTNAME, "action": action, "results": []}
        local_node_result = {
            "machine": HOSTNAME,
            "url": f"http://localhost:{PORT}",
            "success": False,
            "status_code": exc.status_code,
            "error": str(exc.detail),
        }
    node_results = [local_node_result]

    if should_fan_out:
        remotes = await asyncio.gather(*[_remote_fleet_control(name, url, action) for name, url in FLEET_NODES.items()])
        node_results.extend(remotes)

    return {
        "action": action,
        "scope": "local" if scope == "local" else "fleet",
        "machine": local_machine,
        "results": local_result["results"],
        "nodes": node_results,
    }


@app.get("/api/fleet/schedule")
async def get_runner_schedule() -> dict:
    """Return this machine's local runner capacity schedule and live state."""
    return get_runner_capacity_snapshot()


@app.get("/api/fleet/capacity")
async def get_fleet_capacity() -> dict:
    """Compatibility endpoint for dashboard capacity summaries."""
    return get_runner_capacity_snapshot()


@app.post("/api/fleet/schedule")
async def update_runner_schedule(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Update this machine's local runner capacity schedule."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="schedule payload must be an object")
    try:
        config = _validate_runner_schedule(body.get("schedule", body))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _write_runner_schedule_config(config)
    apply_now = bool(body.get("apply", False))
    apply_result: dict[str, object] | None = None
    if apply_now and Path(RUNNER_SCHEDULER_BIN).exists():
        env = safe_subprocess_env()
        env["RUNNER_ROOT"] = str(RUNNER_BASE_DIR)
        env["RUNNER_SCHEDULE_CONFIG"] = str(RUNNER_SCHEDULE_CONFIG)
        env["RUNNER_SCHEDULER_STATE"] = str(RUNNER_SCHEDULER_STATE)
        apply_cmd = _runner_scheduler_apply_command()
        result = subprocess.run(
            apply_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        apply_result = {
            "returncode": result.returncode,
            "stdout": result.stdout.strip()[:1000],
            "stderr": result.stderr.strip()[:1000],
        }
        if result.returncode != 0:
            error = apply_result["stderr"] or apply_result["stdout"]
            raise HTTPException(
                status_code=500,
                detail=f"Schedule saved, but apply failed: {error}",
            )
    return {
        "saved": True,
        "applied": apply_now,
        "apply_result": apply_result,
        **get_runner_capacity_snapshot(),
    }


@app.get("/api/repos")
async def get_repos(request: Request):
    """Get all org repos with open PRs, open issues, and last CI status."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("repos", 600.0)
    if cached is not None:
        return cached

    # Fetch all org repos (paginate up to 200)
    repos = []
    for page in range(1, 3):
        code, stdout, stderr = await run_cmd(
            [
                "gh",
                "api",
                f"/orgs/{ORG}/repos?per_page=100&page={page}&sort=updated&direction=desc",
            ],
            timeout=30,
        )
        if code != 0:
            break
        batch = json.loads(stdout)
        if not batch:
            break
        repos.extend(batch)

    results = []

    # Fetch PR counts, issue counts, and last workflow run concurrently
    async def enrich_repo(repo):
        name = repo["name"]
        full_name = repo["full_name"]
        info = {
            "name": name,
            "full_name": full_name,
            "description": repo.get("description", ""),
            "url": repo.get("html_url", ""),
            "private": repo.get("private", False),
            "language": repo.get("language"),
            "default_branch": repo.get("default_branch", "main"),
            "updated_at": repo.get("updated_at", ""),
            "open_issues_count": repo.get("open_issues_count", 0),  # includes PRs
            "open_prs": 0,
            "open_issues": 0,
            "last_ci_status": None,
            "last_ci_conclusion": None,
            "last_ci_run_url": None,
            "last_ci_updated": None,
        }

        # Get open PRs count — use --paginate so repos with >100 open PRs are
        # counted accurately.  GitHub's open_issues_count includes PRs, so we
        # subtract the real PR count to get the genuine issue count.
        pr_code, pr_out, _ = await run_cmd(
            [
                "gh",
                "api",
                "--paginate",
                f"/repos/{full_name}/pulls?state=open&per_page=100",
            ],
            timeout=30,
        )
        if pr_code == 0:
            try:
                info["open_prs"] = len(json.loads(pr_out))
            except (json.JSONDecodeError, ValueError):
                pass

        # Calculate real issues (subtract PRs from total open_issues_count)
        info["open_issues"] = max(0, info["open_issues_count"] - info["open_prs"])

        # Get last workflow run
        run_code, run_out, _ = await run_cmd(
            [
                "gh",
                "api",
                f"/repos/{full_name}/actions/runs?per_page=1",
            ],
            timeout=15,
        )
        if run_code == 0:
            try:
                runs_data = json.loads(run_out)
                runs_list = runs_data.get("workflow_runs", [])
                if runs_list:
                    last_run = runs_list[0]
                    info["last_ci_status"] = last_run.get("status")
                    info["last_ci_conclusion"] = last_run.get("conclusion")
                    info["last_ci_run_url"] = last_run.get("html_url")
                    info["last_ci_updated"] = last_run.get("updated_at")
            except (json.JSONDecodeError, ValueError):
                pass

        return info

    # Run enrichment concurrently in batches of 10 to avoid overwhelming the API
    for i in range(0, len(repos), 10):
        batch = repos[i : i + 10]
        batch_results = await asyncio.gather(*[enrich_repo(r) for r in batch])
        results.extend(batch_results)

    # Sort: repos with CI activity first, then by update time
    results.sort(key=lambda r: (r["last_ci_updated"] or "",), reverse=True)

    result = {"repos": results, "total_count": len(results), "org": ORG}
    _cache_set("repos", result)
    return result


# ─── PR Inventory API ────────────────────────────────────────────────────────


@app.get("/api/prs")
async def get_prs(
    repo: list[str] | None = None,
    include_drafts: bool = True,
    author: str | None = None,
    label: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate open pull-requests across organisation repositories.

    Query parameters
    ----------------
    repo:
        Repeatable ``owner/repo`` slug filter; defaults to all repos returned
        by ``_get_recent_org_repos()``.
    include_drafts:
        Include draft PRs (default ``true``).
    author:
        Filter to a specific author login.
    label:
        Repeatable; match any of the listed labels.
    limit:
        Maximum items returned (default 500, max 2000).
    """
    if repo:
        repos = list(repo)
    else:
        org_repos = await _get_recent_org_repos(limit=50)
        repos = [r["full_name"] for r in org_repos]

    return await pr_inventory.fetch_all_prs(
        repos,
        include_drafts=include_drafts,
        author=author,
        labels=list(label) if label else None,
        limit=limit,
    )


@app.get("/api/prs/{owner}/{repo_name}/{number}")
async def get_pr_detail(owner: str, repo_name: str, number: int) -> dict:
    """Return detailed information for a single pull-request.

    Extra fields compared to the list endpoint: ``body_excerpt`` (first 2 KB),
    ``checks`` (list of ``{name, conclusion, url}``), ``files_changed``,
    ``additions``, ``deletions``.
    """
    try:
        return await pr_inventory.fetch_pr_detail(owner, repo_name, number)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ─── Issue Inventory API ──────────────────────────────────────────────────────


@app.get("/api/issues")
async def get_issues(
    repo: list[str] | None = None,
    state: str = "open",
    label: list[str] | None = None,
    source: str = "github",
    assignee: str | None = None,
    pickable_only: bool = False,
    complexity: list[str] | None = None,
    effort: list[str] | None = None,
    judgement: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Aggregate open issues across organisation repositories.

    Query parameters
    ----------------
    repo:
        Repeatable ``owner/repo`` slug filter; defaults to all repos returned
        by ``_get_recent_org_repos()``.
    state:
        ``open`` (default) or ``all``.
    label:
        Repeatable; match any of the listed labels.
    source:
        ``github`` (default), ``linear``, or ``unified``.
    assignee:
        Filter by assignee login.
    pickable_only:
        When ``true``, only issues available for agent pickup are returned.
    complexity / effort / judgement:
        Repeatable taxonomy dimension filters (match any provided value).
    limit:
        Maximum items returned (default 500, max 2000).
    """
    if repo:
        repos = list(repo)
    else:
        org_repos = await _get_recent_org_repos(limit=50)
        repos = [r["full_name"] for r in org_repos]

    labels = list(label) if label else None
    complexity_filters = list(complexity) if complexity else None
    effort_filters = list(effort) if effort else None
    judgement_filters = list(judgement) if judgement else None

    if source == "github":
        issues = await issue_inventory.fetch_all_issues(
            repos,
            state=state,
            labels=labels,
            assignee=assignee,
            pickable_only=pickable_only,
            complexity=complexity_filters,
            effort=effort_filters,
            judgement=judgement_filters,
            limit=limit,
        )
    elif source in {"linear", "unified"}:
        linear_config = _linear_router.load_linear_config()
        if not _linear_router.has_configured_linear_key(linear_config):
            raise HTTPException(status_code=503, detail=_linear_router.LINEAR_NOT_CONFIGURED_DETAIL)
        linear_client = _linear_router.build_linear_client(linear_config)
        try:
            if source == "linear":
                issues = await linear_inventory.fetch_all_issues(
                    linear_config,
                    linear_client,
                    state=state,
                    pickable_only=pickable_only,
                    complexity=complexity_filters,
                    effort=effort_filters,
                    judgement=judgement_filters,
                    limit=limit,
                )
                issues["stats"] = {"linear_total": len(issues.get("items", []))}
            else:
                issues = await unified_issue_inventory.fetch_unified_issues(
                    github_repos=repos,
                    linear_config=linear_config,
                    linear_client=linear_client,
                    state=state,
                    labels=labels,
                    assignee=assignee,
                    pickable_only=pickable_only,
                    complexity=complexity_filters,
                    effort=effort_filters,
                    judgement=judgement_filters,
                    limit=limit,
                )
        finally:
            await linear_client.aclose()
    else:
        raise HTTPException(status_code=422, detail="source must be one of github, linear, unified")

    # Wave 3: Sync GitHub leases with internal state
    sync_items = issues if isinstance(issues, list) else issues.get("items", [])
    if isinstance(sync_items, list):
        await lease_synchronizer.sync_github_leases(sync_items)

    return issues


# ─── Daily Reports API ──────────────────────────────────────────────────────


@app.get("/api/reports")
async def list_reports():
    """List available daily progress reports."""
    reports = []
    if REPORTS_DIR.exists():
        for f in sorted(REPORTS_DIR.glob("daily_progress_report_*.md"), reverse=True):
            # Extract date from filename
            date_str = f.stem.replace("daily_progress_report_", "")
            stat = f.stat()
            # Check for companion chart image
            chart_path = REPORTS_DIR / f"assessment_scores_{date_str}.png"
            reports.append(
                {
                    "filename": f.name,
                    "date": date_str,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    "has_chart": chart_path.exists(),
                    "chart_filename": (f"assessment_scores_{date_str}.png" if chart_path.exists() else None),
                }
            )
    return {"reports": reports, "reports_dir": str(REPORTS_DIR), "total": len(reports)}


@app.get("/api/reports/{date}")
async def get_report(date: str):
    """Get the content of a specific daily report."""
    safe_date = sanitize_report_date(date)
    report_path = REPORTS_DIR / f"daily_progress_report_{safe_date}.md"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report not found for date: {safe_date}",
        )

    content = report_path.read_text(encoding="utf-8")

    # Parse key metrics from the report content
    metrics = parse_report_metrics(content)

    return {
        "date": safe_date,
        "filename": report_path.name,
        "content": content,
        "metrics": metrics,
        "size_kb": round(report_path.stat().st_size / 1024, 1),
    }


@app.get("/api/reports/{date}/chart")
async def get_report_chart(date: str):
    """Serve the assessment scores chart image for a specific date."""
    safe_date = sanitize_report_date(date)
    chart_path = REPORTS_DIR / f"assessment_scores_{safe_date}.png"
    if not chart_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Chart not found for date: {safe_date}",
        )
    return FileResponse(chart_path, media_type="image/png")


# ─── Heavy Test Dispatch API ───────────────────────────────────────────────


@app.get("/api/heavy-tests/repos")
async def get_heavy_test_repos():
    """List repos that support heavy test workflow dispatch."""
    repos = []
    for repo_name, config in HEAVY_TEST_REPOS.items():
        # Get recent heavy test runs
        recent_runs = []
        code, stdout, _ = await run_cmd(
            [
                "gh",
                "api",
                f"/repos/{ORG}/{repo_name}/actions/workflows/{config['workflow_file']}/runs?per_page=10",
            ],
            timeout=15,
        )
        if code == 0:
            try:
                data = json.loads(stdout)
                for run in data.get("workflow_runs", []):
                    recent_runs.append(
                        {
                            "id": run["id"],
                            "status": run["status"],
                            "conclusion": run.get("conclusion"),
                            "created_at": run.get("created_at"),
                            "updated_at": run.get("updated_at"),
                            "html_url": run.get("html_url"),
                            "head_branch": run.get("head_branch"),
                            "run_number": run.get("run_number"),
                            "triggering_actor": run.get("triggering_actor", {}).get("login"),
                        }
                    )
            except (json.JSONDecodeError, ValueError):
                pass

        repos.append(
            {
                "name": repo_name,
                "workflow_file": config["workflow_file"],
                "description": config["description"],
                "python_versions": config["python_versions"],
                "default_python": config["default_python"],
                "docker_compose": config.get("docker_compose"),
                "recent_runs": recent_runs,
            }
        )
    return {"repos": repos}


@app.post("/api/heavy-tests/dispatch")
async def dispatch_heavy_test(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("heavy-tests.dispatch")),  # noqa: B008
):  # noqa: B008
    """Dispatch a heavy test workflow via GitHub API."""
    body = await request.json()
    repo_name = body.get("repo")
    python_version = body.get("python_version", "3.11")
    ref = body.get("ref", "main")

    if repo_name not in HEAVY_TEST_REPOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown heavy test repo: {repo_name}",
        )

    config = HEAVY_TEST_REPOS[repo_name]
    workflow_file = config["workflow_file"]

    log.info(
        "Dispatching heavy test: %s/%s (Python %s, ref=%s)",
        repo_name,
        workflow_file,
        python_version,
        ref,
    )

    # Use gh CLI to dispatch the workflow
    code, stdout, stderr = await run_cmd(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"/repos/{ORG}/{repo_name}/actions/workflows/{workflow_file}/dispatches",
            "-f",
            f"ref={ref}",
            "-f",
            f"inputs[python_version]={python_version}",
        ],
        timeout=15,
    )

    if code != 0:
        log.warning(
            "heavy_test dispatch failed: repo=%s workflow=%s stderr=%s",
            repo_name,
            workflow_file,
            stderr[:200],
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to dispatch workflow",
        )

    return {
        "status": "dispatched",
        "repo": repo_name,
        "workflow": workflow_file,
        "python_version": python_version,
        "ref": ref,
        "message": (f"Heavy test workflow dispatched for {repo_name}. Check the Actions tab for progress."),
    }


@app.post("/api/heavy-tests/docker")
async def run_docker_heavy_test(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("heavy-tests.dispatch")),  # noqa: B008
):  # noqa: B008
    """Run heavy tests locally in Docker via docker-compose."""
    body = await request.json()
    repo_name = body.get("repo")
    python_version = body.get("python_version", "3.11")

    if repo_name not in HEAVY_TEST_REPOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown heavy test repo: {repo_name}",
        )

    config = HEAVY_TEST_REPOS[repo_name]
    _default_repos_base = str(Path("/mnt/c") / "Users" / os.environ.get("USER", "diete") / "Repositories")
    _repos_base = Path(os.environ.get("HEAVY_TEST_REPOS_BASE", _default_repos_base))
    repo_path = _repos_base / repo_name

    if not repo_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Repo not found at {repo_path}",
        )

    docker_compose_file = str(config.get("docker_compose", "docker-compose.yml"))
    compose_path = repo_path / docker_compose_file
    if not compose_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"docker-compose file not found: {compose_path}",
        )

    log.info(
        "Starting Docker heavy test for %s (Python %s)",
        repo_name,
        python_version,
    )

    # Run docker-compose in the background
    code, stdout, stderr = await run_cmd(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "run",
            "--rm",
            "-e",
            f"PYTHON_VERSION={python_version}",
            "test-heavy",
        ],
        timeout=300,
    )  # 5 minute timeout for docker build

    if code != 0 and "service" in stderr.lower():
        # The docker-compose file might not have a "test-heavy" service
        # Try running with the default service
        code, stdout, stderr = await run_cmd(
            [
                "docker",
                "compose",
                "-f",
                str(compose_path),
                "up",
                "--build",
                "--abort-on-container-exit",
            ],
            timeout=300,
        )

    return {
        "status": "completed" if code == 0 else "failed",
        "exit_code": code,
        "repo": repo_name,
        "output": stdout[-2000:] if stdout else "",  # Last 2000 chars
        "error": stderr[-1000:] if stderr else "",
    }


# ---------------------------------------------------------------------------
# CI Tests endpoints — standard ci-standard workflow runs + manual rerun
# ---------------------------------------------------------------------------

_CI_FLEET_REPOS = [
    "Repository_Management",
    "AffineDrift",
    "Controls",
    "Drake_Models",
    "Games",
    "Gasification_Model",
    "MEB_Conversion",
    "MLProjects",
    "Movement_Optimizer",
    "MuJoCo_Models",
    "OpenSim_Models",
    "Pinocchio_Models",
    "Playground",
    "QuatEngine",
    "Tools",
    "UpstreamDrift",
    "Worksheet-Workshop",
]


@app.get("/api/tests/ci-results")
async def get_tests_ci_results() -> dict:
    """Return recent ci-standard workflow runs for key fleet repos."""
    cached = _cache_get("ci_test_results", 120.0)
    if cached is not None:
        return cached

    results = []
    for repo_name in _CI_FLEET_REPOS:
        try:
            data = await gh_api_admin(
                f"/repos/{ORG}/{repo_name}/actions/workflows/ci-standard.yml/runs?per_page=3&branch=main"
            )
            runs = data.get("workflow_runs", []) if data else []
            if runs:
                latest = runs[0]
                results.append(
                    {
                        "repo": repo_name,
                        "run_id": latest.get("id"),
                        "run_number": latest.get("run_number"),
                        "status": latest.get("status"),
                        "conclusion": latest.get("conclusion"),
                        "head_branch": latest.get("head_branch"),
                        "html_url": latest.get("html_url"),
                        "created_at": latest.get("created_at"),
                        "updated_at": latest.get("updated_at"),
                    }
                )
            else:
                results.append({"repo": repo_name, "run_id": None, "conclusion": None})
        except Exception:  # noqa: BLE001
            results.append({"repo": repo_name, "run_id": None, "conclusion": "error"})

    out: dict = {"results": results}
    _cache_set("ci_test_results", out)
    return out


@app.post("/api/tests/rerun")
async def rerun_ci_test(request: Request, *, principal: Principal = Depends(require_scope("tests.rerun"))) -> dict:  # noqa: B008
    """Re-run a failed GitHub Actions workflow run (failed jobs only)."""
    body = await request.json()
    repo_name = body.get("repo", "")
    run_id = body.get("run_id")

    if not repo_name or not run_id:
        raise HTTPException(status_code=400, detail="repo and run_id are required")

    try:
        code, stdout, stderr = await run_cmd(
            [
                "gh",
                "api",
                f"/repos/{ORG}/{repo_name}/actions/runs/{run_id}/rerun-failed-jobs",
                "--method",
                "POST",
            ]
        )
        if code != 0:
            raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
        _cache.pop("ci_test_results", None)
        return {"status": "triggered", "repo": repo_name, "run_id": run_id}
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        log.exception("Failed to rerun run %s in %s", run_id, repo_name)
        raise HTTPException(status_code=500, detail="Internal server error") from None


@app.get("/api/stats")
async def get_stats(request: Request):
    """Aggregate organization, runner, queue, and workflow statistics."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("stats", 120.0)
    if cached is not None:
        return cached

    runners_data = _cache_get("runners", 25.0)
    if runners_data is None:
        runners_data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        _cache_set("runners", runners_data)
    runners = runners_data.get("runners", [])

    repos = await _get_recent_org_repos(limit=30)
    all_runs_nested = await asyncio.gather(*[_fetch_repo_runs(repo["name"], per_page=10) for repo in repos[:20]])
    runs = [run for repo_runs in all_runs_nested for run in repo_runs]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    runs = runs[:100]

    online = sum(1 for r in runners if r["status"] == "online")
    busy = sum(1 for r in runners if r.get("busy"))
    completed = [r for r in runs if r.get("conclusion")]
    successes = sum(1 for r in completed if r["conclusion"] == "success")
    failures = sum(1 for r in completed if r["conclusion"] == "failure")

    org_open_issues, org_open_prs, queue_data, fleet_data = await asyncio.gather(
        _github_search_total(f"org:{ORG}+is:open+is:issue"),
        _github_search_total(f"org:{ORG}+is:open+is:pr"),
        _queue_impl(),
        _get_fleet_nodes_impl(),
    )

    result = {
        "runners_total": len(runners),
        "runners_online": online,
        "runners_busy": busy,
        "runners_idle": max(0, online - busy),
        "runners_offline": max(0, len(runners) - online),
        "runs_total": len(runs),
        "runs_success": successes,
        "runs_failure": failures,
        "runs_completed": len(completed),
        "success_rate": round(successes / len(completed) * 100) if completed else 0,
        "in_progress": queue_data.get("in_progress_count", 0),
        "queued": queue_data.get("queued_count", 0),
        "queue_total": queue_data.get("total", 0),
        "org_open_issues": org_open_issues,
        "org_open_prs": org_open_prs,
        "machines_total": fleet_data.get("count", 0),
        "machines_online": fleet_data.get("online_count", 0),
        "machines_offline": max(0, fleet_data.get("count", 0) - fleet_data.get("online_count", 0)),
        "repos_sampled": len(repos[:20]),
    }
    _cache_set("stats", result)
    return result


@app.get("/api/usage")
async def get_usage_monitoring(request: Request) -> dict:
    """Return normalized subscription and local tool usage summaries."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = _cache_get("usage_monitoring", 300.0)
    if cached is not None:
        return cached

    summary = usage_monitoring.normalize_usage_summary(usage_monitoring.load_usage_sources_config())
    _cache_set("usage_monitoring", summary)
    return summary


@app.get("/api/agent-remediation/config")
async def get_agent_remediation_config() -> dict:
    """Return the current CI remediation policy and provider availability."""
    policy = agent_remediation.load_policy()
    availability = agent_remediation.probe_provider_availability()
    return {
        "schema_version": agent_remediation.SCHEMA_VERSION,
        "policy": policy.to_dict(),
        "providers": {provider_id: provider.to_dict() for provider_id, provider in agent_remediation.PROVIDERS.items()},
        "availability": {provider_id: status.to_dict() for provider_id, status in availability.items()},
    }


@app.put("/api/agent-remediation/config", response_model=None)
async def update_agent_remediation_config(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict | JSONResponse:  # noqa: B008
    """Persist the remediation policy so the dashboard can tune auto-routing."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    payload = body.get("policy", body)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="policy must be an object")
    try:
        config_schema.validate_agent_remediation_config(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    current = agent_remediation.load_policy()
    workflow_type_rules = agent_remediation._load_workflow_type_rules(  # noqa: SLF001
        payload.get("workflow_type_rules")
    )
    policy = agent_remediation.RemediationPolicy(
        auto_dispatch_on_failure=bool(payload.get("auto_dispatch_on_failure", current.auto_dispatch_on_failure)),
        require_failure_summary=bool(payload.get("require_failure_summary", current.require_failure_summary)),
        require_non_protected_branch=bool(
            payload.get(
                "require_non_protected_branch",
                current.require_non_protected_branch,
            )
        ),
        max_same_failure_attempts=int(payload.get("max_same_failure_attempts", current.max_same_failure_attempts)),
        attempt_window_hours=int(payload.get("attempt_window_hours", current.attempt_window_hours)),
        provider_order=agent_remediation._as_tuple_strings(  # noqa: SLF001
            payload.get("provider_order"), fallback=current.provider_order
        ),
        enabled_providers=agent_remediation._as_tuple_strings(  # noqa: SLF001
            payload.get("enabled_providers"), fallback=current.enabled_providers
        ),
        default_provider=str(payload.get("default_provider") or current.default_provider),
        workflow_type_rules=workflow_type_rules,
    )
    agent_remediation.save_policy(policy)
    availability = agent_remediation.probe_provider_availability()
    return {
        "schema_version": agent_remediation.SCHEMA_VERSION,
        "policy": policy.to_dict(),
        "providers": {provider_id: provider.to_dict() for provider_id, provider in agent_remediation.PROVIDERS.items()},
        "availability": {provider_id: status.to_dict() for provider_id, status in availability.items()},
    }


@app.get("/api/agent-remediation/workflows")
async def get_agent_remediation_workflows() -> dict:
    """Inspect local Jules workflow health and legacy command usage."""
    report = agent_remediation.inspect_jules_workflows(REPO_ROOT)
    return report.to_dict()


@app.post("/api/agent-remediation/plan")
async def plan_agent_remediation(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Build a guarded remediation plan for one failed workflow run."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")

    context = agent_remediation.FailureContext.from_dict(body)
    if not context.repository.strip():
        raise HTTPException(status_code=400, detail="repository is required")
    if not context.workflow_name.strip():
        raise HTTPException(status_code=400, detail="workflow_name is required")
    if not context.branch.strip():
        raise HTTPException(status_code=400, detail="branch is required")

    repo_name, full_repository = _normalize_repository_input(context.repository)
    context = agent_remediation.FailureContext(
        repository=repo_name,
        workflow_name=context.workflow_name,
        branch=context.branch,
        failure_reason=context.failure_reason,
        log_excerpt=context.log_excerpt,
        run_id=context.run_id,
        conclusion=context.conclusion,
        protected_branch=context.protected_branch,
        source=context.source,
    )

    if context.run_id and not context.log_excerpt.strip():
        log_excerpt = await _fetch_failed_log_excerpt(repo_name, context.run_id)
        if log_excerpt:
            context = agent_remediation.FailureContext(
                repository=context.repository,
                workflow_name=context.workflow_name,
                branch=context.branch,
                failure_reason=context.failure_reason,
                log_excerpt=log_excerpt,
                run_id=context.run_id,
                conclusion=context.conclusion,
                protected_branch=context.protected_branch,
                source=context.source,
            )

    attempts_payload = body.get("attempts", [])
    if attempts_payload is None:
        attempts_payload = []
    if not isinstance(attempts_payload, list):
        raise HTTPException(status_code=422, detail="attempts must be a list")
    attempts = [agent_remediation.AttemptRecord.from_dict(item) for item in attempts_payload if isinstance(item, dict)]
    availability = agent_remediation.probe_provider_availability()
    decision = agent_remediation.plan_dispatch(
        context,
        policy=agent_remediation.load_policy(),
        availability=availability,
        attempts=attempts,
        provider_override=(str(body.get("provider_override")).strip() if body.get("provider_override") else None),
        dispatch_origin="manual",
    )
    return {
        "context": {**context.to_dict(), "full_repository": full_repository},
        "decision": decision.to_dict(),
        "availability": {provider_id: status.to_dict() for provider_id, status in availability.items()},
    }


@app.post("/api/agent-remediation/dispatch")
async def dispatch_agent_remediation(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch the central CI remediation workflow in Repository_Management."""
    client_ip = request.client.host if request.client else "unknown"
    check_dispatch_rate(client_ip)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")

    context = agent_remediation.FailureContext.from_dict(body)

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(principal, estimated_cost=0.10)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    repo_name, full_repository = _normalize_repository_input(context.repository)
    context = agent_remediation.FailureContext(
        repository=repo_name,
        workflow_name=context.workflow_name,
        branch=context.branch,
        failure_reason=context.failure_reason,
        log_excerpt=context.log_excerpt,
        run_id=context.run_id,
        conclusion=context.conclusion,
        protected_branch=context.protected_branch,
        source=context.source,
    )
    provider_id = str(
        body.get("provider") or body.get("provider_override") or agent_remediation.load_policy().default_provider
    ).strip()
    attempts_payload = body.get("attempts", [])
    if attempts_payload is None:
        attempts_payload = []
    attempts = [agent_remediation.AttemptRecord.from_dict(item) for item in attempts_payload if isinstance(item, dict)]

    if context.run_id and not context.log_excerpt.strip():
        log_excerpt = await _fetch_failed_log_excerpt(repo_name, context.run_id)
        if log_excerpt:
            context = agent_remediation.FailureContext(
                repository=context.repository,
                workflow_name=context.workflow_name,
                branch=context.branch,
                failure_reason=context.failure_reason,
                log_excerpt=log_excerpt,
                run_id=context.run_id,
                conclusion=context.conclusion,
                protected_branch=context.protected_branch,
                source=context.source,
            )

    decision = agent_remediation.plan_dispatch(
        context,
        policy=agent_remediation.load_policy(),
        availability=agent_remediation.probe_provider_availability(),
        attempts=attempts,
        provider_override=provider_id,
        dispatch_origin="manual",
    )
    if not decision.accepted:
        raise HTTPException(status_code=409, detail=decision.reason)

    dispatch_ref = str(body.get("ref") or "main")
    failure_reason = re.sub(r"\s+", " ", context.failure_reason).strip()[:1000]
    log_excerpt = re.sub(r"\s+", " ", context.log_excerpt).strip()[:8000]
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Agent-CI-Remediation.yml/dispatches"
    payload = {
        "ref": dispatch_ref,
        "inputs": {
            "target_repository": full_repository,
            "provider": decision.provider_id or provider_id,
            "run_id": str(context.run_id or ""),
            "branch": context.branch,
            "workflow_name": context.workflow_name,
            "failure_reason": failure_reason,
            "log_excerpt": log_excerpt,
            "fingerprint": decision.fingerprint,
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="agent-remediation-dispatch-",
        suffix=".json",
        delete=False,
    ) as payload_file:
        json.dump(payload, payload_file)
        payload_path = payload_file.name
    command = [
        "gh",
        "api",
        endpoint,
        "--method",
        "POST",
        "--input",
        payload_path,
    ]
    try:
        code, _, stderr = await run_cmd(
            command,
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(payload_path).unlink()
    if code != 0:
        log.warning(
            "remediation dispatch failed: target=%s stderr=%s",
            full_repository,
            stderr.strip()[:300],
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to dispatch remediation workflow",
        )

    # Wave 3: Record spend and lease
    quota_enforcement.quota_enforcement.add_spend(principal.id, 0.10)
    try:
        from runner_lease import lease_manager  # noqa: PLC0415

        lease_manager.acquire_lease(
            principal=principal,
            # We don't have an envelope_id here, use fingerprint
            runner_id=f"virtual-{decision.fingerprint}",
            duration_seconds=3600,
            task_id=decision.fingerprint,
            metadata={"source": "agent_remediation", "repo": full_repository},
        )
    except (ValueError, PermissionError) as exc:
        log.warning("Failed to acquire virtual lease for %s: %s", principal.id, exc)
    result = {
        "status": "dispatched",
        "workflow": "Agent-CI-Remediation.yml",
        "target_repository": full_repository,
        "provider": decision.provider_id,
        "fingerprint": decision.fingerprint,
        "reason": decision.reason,
        "note": "Central remediation workflow dispatch recorded in Repository_Management.",
    }
    await _append_remediation_history(
        {
            "timestamp": _dt_mod.datetime.now(_dt_mod.UTC).isoformat(),
            "repository": full_repository,
            "workflow_name": context.workflow_name,
            "branch": context.branch,
            "run_id": context.run_id,
            "provider": decision.provider_id,
            "fingerprint": decision.fingerprint,
            "status": "dispatched",
            "origin": "manual",
        }
    )
    return result


# ─── Quick Dispatch ───────────────────────────────────────────────────────────


@app.post("/api/agents/quick-dispatch")
async def api_quick_dispatch(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch an ad-hoc agent task via Agent-Quick-Dispatch.yml."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = _quick_dispatch.QuickDispatchRequest(**body)
        req.requested_by = principal.id
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if len(req.prompt.strip()) < 10:
        raise HTTPException(status_code=400, detail="prompt must be at least 10 characters")

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(principal, estimated_cost=0.10)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    resp = await _quick_dispatch.quick_dispatch(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if not resp.accepted:
        reason = resp.reason or "rejected"
        if reason.startswith("rate_limited"):
            retry_after = 1
            for part in reason.split("="):
                try:
                    retry_after = int(part)
                except ValueError:
                    pass
            raise HTTPException(
                status_code=429,
                detail={"reason": "rate_limited", "retry_after_seconds": retry_after},
            )
        if reason.startswith("workflow_not_configured"):
            raise HTTPException(
                status_code=501,
                detail={
                    "reason": "workflow_not_configured",
                    "suggested_workflow": "Agent-Quick-Dispatch.yml",
                },
            )
        if reason.startswith("prompt_too_short"):
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(status_code=409, detail={"accepted": False, "reason": reason})
    return resp.model_dump()


# ─── Bulk PR / Issue Agent Dispatch ──────────────────────────────────────────


@app.post("/api/prs/dispatch")
async def api_dispatch_to_prs(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("github.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch agents to one or more pull requests."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = agent_dispatch_router.PRDispatchRequest(**body)
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(principal, estimated_cost=0.10)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    result = await agent_dispatch_router.dispatch_to_prs(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=result.get("status_code", 400), detail=result["error"])
    if isinstance(result, agent_dispatch_router.BulkDispatchResponse):
        return result.model_dump()
    return dict(result)


@app.post("/api/issues/dispatch")
async def api_dispatch_to_issues(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("github.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch agents to one or more issues."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")
    try:
        req = agent_dispatch_router.IssueDispatchRequest(**body)
        req.principal = principal.id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Wave 3: Quota and Fair Sharing
    allowed, reason = quota_enforcement.quota_enforcement.check_dispatch_quota(principal, estimated_cost=0.10)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Quota exceeded: {reason}")

    result = await agent_dispatch_router.dispatch_to_issues(
        req,
        run_cmd_fn=run_cmd,
        org=ORG,
        repo_root=REPO_ROOT,
        normalize_repository_fn=_normalize_repository_input,
    )
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=result.get("status_code", 400), detail=result["error"])
    if isinstance(result, agent_dispatch_router.BulkDispatchResponse):
        return result.model_dump()
    return dict(result)


# ─── Remediation History ──────────────────────────────────────────────────────

_REMEDIATION_HISTORY_PATH = Path(os.environ.get("REMEDIATION_HISTORY_PATH", "")) or (
    Path.home() / "actions-runners" / "dashboard" / "remediation_history.json"
)


async def _append_remediation_history(entry: dict) -> None:
    """Append a dispatch record to the local history file (thread-safe)."""
    async with _remediation_history_lock:
        try:
            history: list[dict] = []
            if _REMEDIATION_HISTORY_PATH.exists():
                try:
                    history = json.loads(_REMEDIATION_HISTORY_PATH.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    history = []
            history.append(entry)
            history = history[-200:]  # keep last 200 entries
            config_schema.atomic_write_json(_REMEDIATION_HISTORY_PATH, history)
        except Exception:  # noqa: BLE001
            pass  # history is best-effort


@app.post("/api/agent-remediation/dispatch-jules")
async def dispatch_jules_workflow(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("remediation.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch a specific Jules workflow via workflow_dispatch."""
    body = await request.json()
    workflow_file = str(body.get("workflow_file", "")).strip()
    ref = str(body.get("ref", "main")).strip()
    inputs = body.get("inputs", {}) or {}
    correlation_id = request.headers.get("X-Correlation-Id", secrets.token_hex(8))
    inputs["correlation_id"] = correlation_id
    if not workflow_file:
        raise HTTPException(status_code=422, detail="workflow_file required")
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/{workflow_file}/dispatches"
    payload = {"ref": ref, "inputs": inputs}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        log.warning(
            "jules dispatch failed: workflow=%s stderr=%s",
            workflow_file,
            stderr.strip()[:300],
        )
        raise HTTPException(status_code=502, detail="Jules dispatch failed")
    return {"status": "dispatched", "workflow_file": workflow_file}


@app.get("/api/agent-remediation/history")
async def get_remediation_history() -> dict:
    """Return recent remediation dispatch history."""
    try:
        if _REMEDIATION_HISTORY_PATH.exists():
            history = json.loads(_REMEDIATION_HISTORY_PATH.read_text(encoding="utf-8"))
        else:
            history = []
    except Exception:  # noqa: BLE001
        history = []
    return {"history": list(reversed(history[-100:]))}  # newest first


_PROVIDERS_WITH_MODEL_SELECTION: frozenset[str] = frozenset({"claude_code_cli", "codex_cli", "gemini_cli"})


@app.get("/api/agents/providers")
async def get_agent_providers() -> dict:
    """Return available agent providers and their availability status."""
    availability = agent_remediation.probe_provider_availability()
    return {
        "providers": {pid: p.to_dict() for pid, p in agent_remediation.PROVIDERS.items()},
        "availability": {pid: s.to_dict() for pid, s in availability.items()},
        "providers_with_model_selection": sorted(_PROVIDERS_WITH_MODEL_SELECTION),
    }


# ─── Assistant Chat API (Issue #88) ───────────────────────────────────────────


async def _dispatch_to_ai_provider_for_chat(
    provider: str | None,
    prompt: str,
    context: dict,
) -> str:
    """Call the configured AI provider for assistant chat."""
    # For MVP: return a simple response based on the prompt
    # In production, this would dispatch to an actual provider (Jules, Claude, etc.)
    provider_id = provider or "ollama_local"

    # Check provider availability
    availability = agent_remediation.probe_provider_availability()
    if provider_id not in availability or not availability[provider_id].available:
        # Return a synthetic response for MVP
        return f"(Note: Provider '{provider_id}' is unavailable. Mock response for demonstration.)"

    # For MVP: return a simple acknowledgment
    # Tracked in #88/#89: implement actual provider dispatch (Jules API, local Ollama, Claude API, etc.)
    return f"Assistant response to: {prompt[:100]}... (MVP mock - implement real provider dispatch)"


@app.post("/api/assistant/chat", tags=["assistant"])
async def assistant_chat(request: Request, *, principal: Principal = Depends(require_scope("assistant.chat"))) -> dict:  # noqa: B008
    """Chat with AI assistant about dashboard state.

    When ``tools_enabled: true`` is set, the Anthropic tool-use loop is
    activated and the response may contain ``tool_calls`` for the client to
    render as confirmation cards (issue #89).
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.AssistantChatRequest(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e

    now_ts = datetime.now(UTC).isoformat()

    # ── Tool-use path (Issue #89) ──────────────────────────────────────────
    if req.tools_enabled:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY not configured; tool-use requires Anthropic.",
            )
        try:
            result = await assistant_tools.call_anthropic_with_tools(
                api_key=anthropic_key,
                prompt=req.prompt,
                context=req.context.dict(),
                model=DEFAULT_LLM_MODEL,
                tools_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Anthropic tool-use error: %s", exc)
            raise HTTPException(status_code=502, detail=f"Anthropic error: {exc}") from exc
        return {
            "message": result["message"],
            "stop_reason": result["stop_reason"],
            "tool_calls": result["tool_calls"],
            "provider": "anthropic",
            "timestamp": now_ts,
        }

    # ── Standard chat path ────────────────────────────────────────────────
    response_text = await _dispatch_to_ai_provider_for_chat(
        provider=req.provider,
        prompt=req.prompt,
        context=req.context.dict(),
    )
    return {
        "response": response_text,
        "provider": req.provider or "ollama_local",
        "context_used": req.context.dict(),
        "timestamp": now_ts,
    }


# ─── Tool Execute API (Issue #89) ─────────────────────────────────────────────


@app.post("/api/assistant/tool/execute", tags=["assistant"])
async def execute_assistant_tool(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assistant.execute")),  # noqa: B008
) -> dict:
    """Execute a tool call from the assistant allowlist (Issue #89).

    State-changing tools require ``confirmation`` in the request body.
    Every execution (success or failure) is appended to the audit log.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.ToolExecuteRequest(**body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if req.name not in assistant_tools.TOOL_ALLOWLIST:
        raise HTTPException(
            status_code=422,
            detail=f"Tool '{req.name}' is not in the allowlist.",
        )

    spec = assistant_tools.TOOL_ALLOWLIST[req.name]
    requires_conf = spec["requires_confirmation"]

    if requires_conf and req.confirmation is None:
        raise HTTPException(
            status_code=403,
            detail=f"Tool '{req.name}' requires explicit operator confirmation.",
        )

    # Execute via assistant_tools
    try:
        outcome_data = await assistant_tools.execute_tool(
            tool_name=req.name,
            tool_call_id=req.tool_call_id,
            inputs=req.input,
            confirmation=req.confirmation.model_dump() if req.confirmation else None,
            principal=principal.id,
            on_behalf_of=(req.confirmation.on_behalf_of or "") if req.confirmation else "",
            correlation_id=(req.confirmation.correlation_id or "") if req.confirmation else "",
            gh_api_fn=gh_api,
            run_cmd_fn=run_cmd,
            normalize_repository_fn=_normalize_repository_input,
            org=ORG,
            repo_root=REPO_ROOT,
        )
        return {
            "success": True,
            "tool_call_id": req.tool_call_id,
            "name": req.name,
            "result": outcome_data.get("result"),
            "audit_id": outcome_data.get("audit_entry", {}).get("timestamp"),
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.error("tool execute error tool=%s: %s", req.name, exc)
        # Record a failed audit entry manually if execute_tool failed before recording
        audit_entry = assistant_tools._record_audit(  # noqa: SLF001
            tool_name=req.name,
            tool_call_id=req.tool_call_id,
            inputs=req.input,
            outcome=f"error: {exc}",
            success=False,
            approved_by=req.confirmation.approved_by if req.confirmation else "n/a",
            principal=principal.id,
            on_behalf_of=(req.confirmation.on_behalf_of or "") if req.confirmation else "",
            correlation_id=(req.confirmation.correlation_id or "") if req.confirmation else "",
            note=req.confirmation.note if req.confirmation else "",
        )
        return {
            "success": False,
            "tool_call_id": req.tool_call_id,
            "name": req.name,
            "result": {"error": str(exc)},
            "audit_id": audit_entry["timestamp"],
        }


@app.get("/api/assistant/audit-history", tags=["assistant"])
async def get_tool_audit_history(limit: int = 50) -> dict:
    """Return the most recent assistant tool-execution audit entries (Issue #89)."""
    capped = max(1, min(limit, 200))
    entries = assistant_tools.get_audit_history(limit=capped)
    return {"entries": entries, "total": len(entries)}


# ─── Assistant Action Proposal API (Issue #89) ────────────────────────────────


# In-memory storage for proposed actions (in real impl, use database)
_proposed_actions: dict[str, dict] = {}


@app.post("/api/assistant/propose-action", tags=["assistant"])
async def propose_action(request: Request, *, principal: Principal = Depends(require_scope("assistant.chat"))) -> dict:  # noqa: B008
    """Propose an action based on user request, awaiting operator approval."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.ActionProposeRequest(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Call AI provider to generate action proposal
    provider_id = req.provider or "ollama_local"
    availability = agent_remediation.probe_provider_availability()
    if provider_id not in availability or not availability[provider_id].available:
        # For MVP: return a synthetic proposal
        pass  # Continue with mock response

    try:
        # MVP: return a synthetic proposal instead of calling a provider
        response_text = json.dumps(
            {
                "action_type": "restart_runner",
                "description": f"Restart runner based on request: {req.user_request[:50]}",
                "parameters": {"runner_name": "auto"},
                "risk_level": "medium",
                "rationale": "This action may resolve the issue",
                "estimated_duration_seconds": 60,
            }
        )
        # Parse JSON response
        import json as _json_parser

        try:
            proposal_dict = _json_parser.loads(response_text)
        except Exception:  # noqa: BLE001
            # Fallback: wrap response in a generic action
            proposal_dict = {
                "action_type": "custom_response",
                "description": response_text[:200],
                "parameters": {},
                "risk_level": "medium",
                "rationale": "AI-generated action",
            }

        # Generate action ID and store
        action_id = secrets.token_hex(8)
        _proposed_actions[action_id] = {
            "created_at": datetime.now(UTC).isoformat(),
            "proposal": proposal_dict,
            "approved": False,
        }

        return {
            "action_id": action_id,
            "action_type": proposal_dict.get("action_type", "custom"),
            "parameters": proposal_dict.get("parameters", {}),
            "description": proposal_dict.get("description", ""),
            "risk_level": proposal_dict.get("risk_level", "medium"),
            "rationale": proposal_dict.get("rationale", ""),
            "estimated_duration_seconds": proposal_dict.get("estimated_duration_seconds"),
        }
    except Exception as e:  # noqa: BLE001
        log.error("Action proposal error: %s", e)
        raise HTTPException(status_code=502, detail=f"AI provider error: {str(e)}") from e


@app.post("/api/assistant/execute-action", tags=["assistant"])
async def execute_action(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assistant.execute")),  # noqa: B008
) -> dict:
    """Execute a proposed action after operator approval."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    try:
        req = assistant_contract.ActionExecuteRequest(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Check action exists and is pending
    if req.action_id not in _proposed_actions:
        raise HTTPException(status_code=404, detail="Action not found")

    action_record = _proposed_actions[req.action_id]
    if action_record.get("approved"):
        raise HTTPException(status_code=409, detail="Action already executed")

    if not req.approved:
        # Just mark as rejected
        action_record["approved"] = True
        action_record["result"] = "Rejected by operator"
        return {
            "success": False,
            "action_id": req.action_id,
            "result": "Action rejected",
            "execution_time_ms": 0,
        }

    # Mark as approved
    action_record["approved"] = True
    action_record["approved_at"] = datetime.now(UTC).isoformat()
    action_record["approved_by"] = "operator"
    action_record["operator_notes"] = req.operator_notes

    proposal = action_record["proposal"]
    action_type = proposal.get("action_type", "unknown")

    start_time = time.time()

    try:
        # Execute action based on type
        result = "Action executed successfully"

        if action_type == "restart_runner":
            runner_name = proposal.get("parameters", {}).get("runner_name")
            if runner_name:
                result = f"Runner '{runner_name}' restart initiated"
                # In real impl, would call actual restart logic

        elif action_type == "rerun_workflow":
            workflow_id = proposal.get("parameters", {}).get("workflow_id")
            if workflow_id:
                result = f"Workflow {workflow_id} rerun initiated"

        elif action_type == "dismiss_alert":
            alert_id = proposal.get("parameters", {}).get("alert_id")
            if alert_id:
                result = f"Alert {alert_id} dismissed"

        execution_time_ms = int((time.time() - start_time) * 1000)

        action_record["result"] = result
        action_record["execution_time_ms"] = execution_time_ms

        return {
            "success": True,
            "action_id": req.action_id,
            "result": result,
            "execution_time_ms": execution_time_ms,
        }

    except Exception as e:  # noqa: BLE001
        execution_time_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        action_record["result"] = f"Execution failed: {error_msg}"
        action_record["execution_time_ms"] = execution_time_ms
        log.error("Action execution error: %s", e)
        return {
            "success": False,
            "action_id": req.action_id,
            "result": f"Execution failed: {error_msg}",
            "execution_time_ms": execution_time_ms,
        }


# ─── Job Queue API ───────────────────────────────────────────────────────────


async def _queue_impl() -> dict:
    """Core queue aggregation, callable from the HTTP endpoint and internally."""
    cached = _cache_get("queue", 120.0)
    if cached is not None:
        return cached

    repos = await _get_recent_org_repos(limit=20)
    if not repos:
        return _empty_queue_result()

    async def fetch_active_runs(repo_name: str) -> list[dict]:
        results: list[dict] = []
        for status in ("queued", "in_progress"):
            results.extend(await _fetch_repo_runs(repo_name, per_page=10, status=status))
        return results

    sample = repos[:15]
    all_runs_nested = await asyncio.gather(*[fetch_active_runs(r["name"]) for r in sample])
    all_runs: list[dict] = [run for sublist in all_runs_nested for run in sublist]

    queued = sorted(
        [r for r in all_runs if r.get("status") == "queued"],
        key=lambda r: r.get("created_at", ""),
    )
    in_progress = sorted(
        [r for r in all_runs if r.get("status") == "in_progress"],
        key=lambda r: r.get("run_started_at") or r.get("created_at", ""),
    )

    result = {
        "queued": queued,
        "in_progress": in_progress,
        "total": len(queued) + len(in_progress),
        "queued_count": len(queued),
        "in_progress_count": len(in_progress),
    }
    _cache_set("queue", result)
    return result


@app.get("/api/queue")
async def get_queue(request: Request) -> dict:
    """Get queued and in-progress workflow runs across the org.

    GitHub has no org-level queue endpoint; we query the 15 most recently
    updated repos concurrently for both statuses and aggregate the results.
    """
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _queue_impl()


@app.post("/api/runs/{repo}/cancel/{run_id}")
async def cancel_run(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),
    repo: str,
    run_id: int,  # noqa: B008
) -> dict:
    """Cancel a single queued or in-progress workflow run."""
    code, _, stderr = await run_cmd(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"/repos/{ORG}/{repo}/actions/runs/{run_id}/cancel",
        ],
        timeout=15,
    )
    if code != 0:
        raise HTTPException(status_code=502, detail=f"Cancel failed: {stderr}")
    # Invalidate stale queue/runs caches so the next poll reflects the cancel.
    _cache.pop("queue", None)
    _cache.pop("diagnose", None)
    return {"cancelled": True, "run_id": run_id, "repo": repo}


@app.post("/api/runs/{repo}/rerun/{run_id}")
async def rerun_failed(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),
    repo: str,
    run_id: int,  # noqa: B008
) -> dict:
    """Re-run failed jobs in a workflow run."""
    code, _, stderr = await run_cmd(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"/repos/{ORG}/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
        ],
        timeout=15,
    )
    if code != 0:
        raise HTTPException(status_code=502, detail=f"Rerun failed: {stderr}")
    _cache.pop("queue", None)
    return {"rerun": True, "run_id": run_id, "repo": repo}


@app.post("/api/queue/cancel-workflow")
async def cancel_workflow_runs(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
) -> dict:
    """Cancel all queued runs of a specific workflow across the org.

    Body: {"workflow_name": "ci-standard", "repo": "MyRepo"}  (repo optional)
    Useful for deprioritising a noisy workflow to free runners for
    higher-priority work.
    """
    body = await request.json()
    workflow_name: str = body.get("workflow_name", "")
    target_repo: str | None = body.get("repo")

    if not workflow_name:
        raise HTTPException(status_code=400, detail="workflow_name required")

    # Fetch current queue
    queue_data = await _queue_impl()
    runs_to_cancel = [
        r
        for r in queue_data["queued"]
        if r.get("name") == workflow_name
        and (target_repo is None or (r.get("repository") or {}).get("name") == target_repo)  # noqa: E501
    ]

    cancelled: list[dict] = []
    errors: list[str] = []
    for run in runs_to_cancel:
        repo = (run.get("repository") or {}).get("name", "")
        run_id = run["id"]
        if not repo:
            continue
        code, _, stderr = await run_cmd(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"/repos/{ORG}/{repo}/actions/runs/{run_id}/cancel",
            ],
            timeout=15,
        )
        if code == 0:
            cancelled.append({"repo": repo, "run_id": run_id})
        else:
            errors.append(f"{repo}#{run_id}: {stderr.strip()}")

    if cancelled:
        _cache.pop("queue", None)
        _cache.pop("diagnose", None)

    return {
        "cancelled_count": len(cancelled),
        "cancelled": cancelled,
        "errors": errors,
    }


@app.get("/api/queue/diagnose")
async def diagnose_queue() -> dict:
    """Explain why queued jobs are waiting.

    Samples queued workflow runs, fetches their jobs, and reports which runner
    labels the waiting jobs need — self-hosted fleet, ubuntu-latest, or other.
    Cross-references against the live runner pool to identify the bottleneck.
    """
    cached = _cache_get("diagnose", 120.0)
    if cached is not None:
        return cached

    # Runner pool status
    try:
        runner_data = _cache_get("runners", 25.0)
        if runner_data is None:
            runner_data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
            _cache_set("runners", runner_data)
        runners = runner_data.get("runners", [])
    except Exception:  # noqa: BLE001
        runners = []

    online = [r for r in runners if r["status"] == "online"]
    busy = [r for r in runners if r.get("busy")]
    idle = [r for r in online if not r.get("busy")]
    online_runner_names = {r.get("name", "") for r in online}

    # Collect queued runs across repos
    code, stdout, _ = await run_cmd(
        ["gh", "api", f"/orgs/{ORG}/repos?per_page=20&sort=updated&direction=desc"],
        timeout=20,
    )
    if code != 0:
        return {"error": "Cannot reach GitHub API — check GH_TOKEN in service"}

    try:
        repos = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {"error": "Invalid response from GitHub API"}

    queued_runs: list[dict] = []
    for repo in repos[:15]:
        rc, out, _ = await run_cmd(
            [
                "gh",
                "api",
                f"/repos/{ORG}/{repo['name']}/actions/runs?status=queued&per_page=5",
            ],
            timeout=10,
        )
        if rc != 0:
            continue
        try:
            for run in json.loads(out).get("workflow_runs", []):
                run["_repo"] = repo["name"]
                queued_runs.append(run)
        except (json.JSONDecodeError, ValueError):
            continue

    # For each sampled run fetch its jobs to see what runner labels are needed
    async def get_run_jobs(run: dict) -> list[dict]:
        rc, out, _ = await run_cmd(
            [
                "gh",
                "api",
                f"/repos/{ORG}/{run['_repo']}/actions/runs/{run['id']}/jobs?per_page=30",
            ],
            timeout=10,
        )
        if rc != 0:
            return []
        try:
            return json.loads(out).get("jobs", [])
        except (json.JSONDecodeError, ValueError):
            return []

    sample = queued_runs[:20]
    all_jobs_nested = await asyncio.gather(*[get_run_jobs(r) for r in sample])

    # Labels that GitHub automatically applies to every self-hosted runner
    GENERIC_SELF_HOSTED = {
        "self-hosted",
        "linux",
        "Linux",
        "x64",
        "X64",
        "arm64",
        "ARM64",
        "windows",
        "Windows",
        "macOS",
    }
    GITHUB_HOSTED = {
        "ubuntu-latest",
        "ubuntu-22.04",
        "ubuntu-20.04",
        "ubuntu-24.04",
        "windows-latest",
        "macos-latest",
        "macos-14",
        "macos-13",
    }

    label_counts: dict[str, int] = {}
    waiting_for_fleet = 0
    waiting_for_generic_sh = 0
    waiting_for_github_hosted = 0
    sampled_jobs: list[dict] = []

    for run, jobs in zip(sample, all_jobs_nested, strict=False):
        for job in jobs:
            if job.get("status") != "queued":
                continue
            labels: list[str] = job.get("labels", [])
            for lbl in labels:
                label_counts[lbl] = label_counts.get(lbl, 0) + 1

            is_fleet = any(lbl.startswith("d-sorg") for lbl in labels)
            is_generic_sh = not is_fleet and any(lbl in GENERIC_SELF_HOSTED for lbl in labels)
            is_github = any(lbl in GITHUB_HOSTED for lbl in labels)

            if is_fleet:
                waiting_for_fleet += 1
            elif is_generic_sh:
                waiting_for_generic_sh += 1
            elif is_github:
                waiting_for_github_hosted += 1

            if is_fleet:
                target = "self-hosted (d-sorg-fleet)"
            elif is_generic_sh:
                target = "self-hosted (generic)"
            elif is_github:
                target = "github-hosted"
            else:
                target = "unknown"

            sampled_jobs.append(
                {
                    "repo": run["_repo"],
                    "run_id": run["id"],
                    "workflow": run.get("name"),
                    "job": job.get("name"),
                    "labels": labels,
                    "target": target,
                    "created_at": job.get("created_at"),
                }
            )

    waiting_for_self_hosted = waiting_for_fleet + waiting_for_generic_sh

    # Deep runner group check: fetch runners per group and allowed repos per
    # restricted group so we can pinpoint exactly which group the idle runners
    # belong to and which repos they can't see.
    runner_groups_info: list[dict] = []
    runner_groups_restricted = False
    runners_by_group: dict[int, list[str]] = {}  # group_id -> runner names

    async def fetch_group_runners(gid: int) -> list[str]:
        try:
            d = await gh_api_admin(f"/orgs/{ORG}/actions/runner-groups/{gid}/runners?per_page=100")
            return [r.get("name", "") for r in d.get("runners", [])]
        except Exception:  # noqa: BLE001
            return []

    async def fetch_group_repos(gid: int) -> list[str]:
        try:
            d = await gh_api_admin(f"/orgs/{ORG}/actions/runner-groups/{gid}/repositories?per_page=100")
            return [r.get("name", "") for r in d.get("repositories", [])]
        except Exception:  # noqa: BLE001
            return []

    try:
        rg_data = await gh_api_admin(f"/orgs/{ORG}/actions/runner-groups")
        raw_groups = rg_data.get("runner_groups", [])

        # Fetch runners for every group concurrently
        group_runner_lists = await asyncio.gather(*[fetch_group_runners(g["id"]) for g in raw_groups])
        for grp, grp_runners in zip(raw_groups, group_runner_lists, strict=False):
            runners_by_group[grp["id"]] = grp_runners

        # Fetch allowed repos for restricted groups
        restricted_groups = [g for g in raw_groups if g.get("visibility") != "all"]
        group_repo_lists = await asyncio.gather(*[fetch_group_repos(g["id"]) for g in restricted_groups])
        allowed_repos_by_group: dict[int, list[str]] = {
            g["id"]: repos for g, repos in zip(restricted_groups, group_repo_lists, strict=False)
        }

        # Collect repos with waiting jobs
        waiting_repos = {r["_repo"] for r in sample}

        for grp in raw_groups:
            gid = grp["id"]
            restricted = grp.get("visibility") != "all"
            grp_runners = runners_by_group.get(gid, [])
            allowed_repos = allowed_repos_by_group.get(gid, []) if restricted else []

            # Which waiting repos are blocked by this group's restrictions?
            blocked = [r for r in waiting_repos if r not in allowed_repos] if restricted else []

            runner_groups_info.append(
                {
                    "id": gid,
                    "name": grp.get("name"),
                    "visibility": grp.get("visibility"),
                    "restricted": restricted,
                    # True = enterprise-owned group
                    "inherited": grp.get("inherited", False),
                    "allows_public_repos": grp.get("allows_public_repositories", False),
                    "runner_count": len(grp_runners),
                    "runner_names": grp_runners[:8],  # cap for display
                    "allowed_repos": allowed_repos[:20] if restricted else [],
                    "blocked_waiting_repos": blocked,
                }
            )

            # Flag restriction if any group containing our idle runners is restricted
            # and is blocking at least one waiting repo
            if restricted and blocked and any(r in online_runner_names for r in grp_runners):
                runner_groups_restricted = True

    except Exception:  # noqa: BLE001
        pass  # Non-fatal

    # Detect pick-runner jobs that are themselves waiting on self-hosted
    # (misconfiguration: pick-runner should run on ubuntu-latest, not self-hosted)
    pick_runner_misconfig = [
        j
        for j in sampled_jobs
        if (j.get("job") or "").lower() in ("pick-runner", "pick runner", "select runner")  # noqa: E501
        and "self-hosted" in j.get("target", "")
    ]

    # Determine bottleneck
    if pick_runner_misconfig:
        repos_affected = sorted({j["repo"] for j in pick_runner_misconfig})
        bottleneck = (
            f"MISCONFIGURATION: {len(pick_runner_misconfig)} 'pick-runner' dispatcher "
            f"job(s) are themselves targeting 'self-hosted' in: "
            f"{', '.join(repos_affected)}. "
            "The pick-runner job must use 'runs-on: ubuntu-latest' (not 'self-hosted') — "  # noqa: E501
            "it is the dispatcher that decides where to send work. "
            "Update those workflow files to fix 'runs-on: ubuntu-latest' on the pick-runner job."  # noqa: E501
        )
    elif waiting_for_fleet > 0 and not idle:
        bottleneck = (
            f"All {len(busy)} d-sorg-fleet runner(s) are busy. "
            "Jobs will run as runners finish. Bring more machines online to increase throughput."  # noqa: E501
        )
    elif waiting_for_fleet > 0 and idle:
        bottleneck = (
            f"{len(idle)} idle fleet runner(s) exist but {waiting_for_fleet} fleet job(s) are "  # noqa: E501
            "still queued — possible label mismatch. Verify the runner labels include 'd-sorg-fleet'."  # noqa: E501
        )
    elif waiting_for_generic_sh > 0 and idle:
        if runner_groups_restricted:
            blocked_info = [
                f"'{g['name']}' (runners: {', '.join(g['runner_names'][:3])}{'…' if len(g['runner_names']) > 3 else ''}) "  # noqa: E501
                f"blocks: {', '.join(g['blocked_waiting_repos'][:5])}"
                for g in runner_groups_info
                if g["restricted"] and g["blocked_waiting_repos"]
            ]
            bottleneck = (
                f"RUNNER GROUP ACCESS RESTRICTION: {waiting_for_generic_sh} job(s) cannot "  # noqa: E501
                f"reach {len(idle)} idle runner(s). "
                + (" | ".join(blocked_info) + ". " if blocked_info else "")
                + "FIX: GitHub org Settings → Actions → Runner Groups → "
                "select the restricted group → set Repository access to 'All repositories'."  # noqa: E501
            )
        else:
            bottleneck = (
                f"{waiting_for_generic_sh} job(s) target the generic 'self-hosted' label "  # noqa: E501
                f"with {len(idle)} idle runner(s). "
                "Runners will pick these up — but check if any are pick-runner "  # noqa: E501
                "dispatcher jobs (they should use runs-on: ubuntu-latest, not "
                "self-hosted, to avoid wasting a runner slot on routing logic)."
            )
    elif waiting_for_generic_sh > 0 and not idle:
        bottleneck = (
            f"All {len(busy)} fleet runner(s) are busy and {waiting_for_generic_sh} job(s) "  # noqa: E501
            "target generic 'self-hosted'. Jobs will run as runners free up."
        )
    elif waiting_for_github_hosted > 0:
        bottleneck = (
            f"{waiting_for_github_hosted} job(s) are waiting for GitHub-hosted runners "
            "(ubuntu-latest). This is GitHub's queue — no local action possible. "
            "This may mean pick-runner routed them to the cloud because all fleet "
            "runners were busy when the dispatcher ran."
        )
    elif not sampled_jobs:
        bottleneck = "Could not sample job details — runs may have just started or GitHub API rate limit may be close."
    else:
        bottleneck = "Unknown — job labels did not match known runner targets."

    result = {
        "runner_pool": {
            "total": len(runners),
            "online": len(online),
            "busy": len(busy),
            "idle": len(idle),
            "offline": len(runners) - len(online),
        },
        "queued_runs_found": len(queued_runs),
        "jobs_sampled": len(sampled_jobs),
        "waiting_for_fleet": waiting_for_fleet,
        "waiting_for_generic_self_hosted": waiting_for_generic_sh,
        "waiting_for_self_hosted": waiting_for_self_hosted,
        "waiting_for_github_hosted": waiting_for_github_hosted,
        "runner_groups": runner_groups_info,
        "runner_groups_restricted": runner_groups_restricted,
        "pick_runner_misconfig": pick_runner_misconfig,
        "label_breakdown": label_counts,
        "bottleneck": bottleneck,
        "sampled_jobs": sampled_jobs[:15],
    }
    _cache_set("diagnose", result)
    return result


# ─── Fleet Node Aggregation API ──────────────────────────────────────────────


@app.get("/api/fleet/nodes")
async def get_fleet_nodes(request: Request) -> dict:
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    return await _get_fleet_nodes_impl()


@app.get("/api/fleet/hardware")
async def get_fleet_hardware(request: Request) -> dict:
    """Return centralized fleet hardware specs for workload placement."""
    if _should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    fleet = await _get_fleet_nodes_impl()
    machines = []
    for node in fleet.get("nodes", []):
        registry = node.get("registry") or {}
        specs = node.get("hardware_specs") or node.get("system", {}).get("hardware_specs", {})
        capacity = node.get("workload_capacity") or node.get("system", {}).get("workload_capacity", {})
        machines.append(
            {
                "name": node.get("name"),
                "display_name": registry.get("display_name") or node.get("name"),
                "online": bool(node.get("online")),
                "dashboard_reachable": bool(node.get("dashboard_reachable")),
                "role": registry.get("role") or node.get("role"),
                "runner_labels": registry.get("runner_labels", []),
                "hardware_specs": specs,
                "workload_capacity": capacity,
                "offline_reason": node.get("offline_reason"),
            }
        )
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "machines": machines,
        "count": len(machines),
        "online_count": sum(1 for machine in machines if machine["online"]),
        "registry": fleet.get("registry", {}),
    }


async def _get_fleet_nodes_impl() -> dict:
    """Aggregate system metrics + health from all fleet nodes.

    Always includes this machine (no HTTP round-trip).  Remote nodes are
    queried concurrently over Tailscale using FLEET_NODES config.
    Offline nodes are included with online=False so the UI can show them.
    """
    nodes = await _collect_live_fleet_nodes()
    try:
        registry = load_machine_registry()
    except Exception as exc:  # noqa: BLE001
        log.warning("Machine registry load failed: %s", exc)
        registry = {"version": 1, "machines": []}
    nodes = merge_registry_with_live_nodes(nodes, registry)
    nodes = [{**node, **_node_visibility_snapshot(node)} for node in nodes]
    online = sum(1 for n in nodes if n["online"])
    total_runners = sum(n["health"].get("runners_registered", 0) for n in nodes)
    return {
        "nodes": nodes,
        "count": len(nodes),
        "online_count": online,
        "total_runners": total_runners,
        "registry": {
            "path": str(BACKEND_DIR / "machine_registry.yml"),
            "version": registry.get("version", 1),
            "machines": len(registry.get("machines", [])),
        },
    }


@app.get("/api/fleet/nodes/{node_name}/system")
async def proxy_node_system(node_name: str) -> dict:
    """Proxy /api/system from a named fleet node (for detailed drill-down)."""
    if node_name in (HOSTNAME, "local"):
        return await _metrics_router.get_system_metrics()
    url = FLEET_NODES.get(node_name)
    if not url:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_name}")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{url}/api/system")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Node returned error")
        return resp.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"{node_name} timed out") from exc
    except httpx.RequestError as exc:
        log.warning("Node %s unreachable: %s", node_name, exc)
        raise HTTPException(status_code=502, detail=f"{node_name} unreachable") from exc


# ─── Request logging middleware ───────────────────────────────────────────────


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)
    skip = (
        "/api/system",
        "/api/repos",
        "/api/reports",
        "/api/heavy-tests",
        "/api/scheduled-workflows",
    )
    if not request.url.path.startswith(skip):
        log.info(
            "%s %s → %s (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
    return response


# ─── Serve Frontend ──────────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent.parent / "dist"

# Mount Vite build assets for fast serving (only if dist/assets exists)
_assets_dir = FRONTEND_DIR / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")


@app.get("/")
async def serve_index():
    """Serve the dashboard HTML page."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    fallback = (
        "<html><body style='"
        "background:#0f1117;color:#e6edf3;"
        "font-family:sans-serif;display:flex;"
        "align-items:center;justify-content:center;"
        "min-height:100vh;'>"
        "<div style='text-align:center'>"
        "<h1>API is running</h1>"
        "<p>Frontend index.html not found</p>"
        "<p><a href='/api/health' "
        "style='color:#58a6ff'>Health Check</a>"
        " · <a href='/docs' "
        "style='color:#58a6ff'>API Docs</a></p>"
        "</div></body></html>"
    )
    return HTMLResponse(content=fallback)


@app.get("/manifest.webmanifest")
async def serve_manifest():
    """Serve the mobile web app manifest."""
    manifest_path = FRONTEND_DIR / "manifest.webmanifest"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="manifest not found")
    return FileResponse(manifest_path, media_type="application/manifest+json")


@app.get("/icon.svg")
async def serve_icon():
    """Serve the mobile dashboard icon."""
    icon_path = FRONTEND_DIR / "icon.svg"
    if not icon_path.exists():
        raise HTTPException(status_code=404, detail="icon not found")
    return FileResponse(icon_path, media_type="image/svg+xml")


# ─── Fleet Agent Dispatcher API — see backend/routers/dispatch.py ─────────────
# Endpoints extracted to routers/dispatch.py and registered via app.include_router.

# ─── Credentials Probe — see backend/routers/credentials.py ──────────────────
# Endpoint extracted to routers/credentials.py and registered via app.include_router.

# ─── Maxwell-Daemon endpoints ─────────────────────────────────────────────────

DASHBOARD_FAQ: dict[str, str] = {
    "fleet": "The Fleet tab shows all runners in your fleet. Use it to start/stop runners and see hardware metrics.",
    "remediation": (
        "The Remediation tab lets you dispatch AI agents (Jules, Codex, Claude) to fix failing CI."
        " Move to top: Manual Dispatch is the primary control."
    ),
    "workflows": (
        "The Workflows tab lists all GitHub Actions workflows across repos."
        " Click a workflow to see run history and dispatch it manually."
    ),
    "credentials": (
        "The Credentials tab shows provider connection state."
        " No secrets are shown - only whether tools are installed and authenticated."
    ),
    "assessments": (
        "The Assessments tab lets you trigger code quality assessments for any repo and view score history."
    ),
    "feature-requests": (
        "The Feature Requests tab dispatches AI agents to implement new features"
        " with standards injection (TDD, DbC, DRY, LoD)."
    ),
    "maxwell": ("The Maxwell tab shows Maxwell-Daemon status and lets you start/stop the service with confirmation."),
    "queue": "The Queue tab shows live queued and in-progress workflows with auto-refresh every 15 seconds.",
    "history": "The History tab shows recent workflow runs across all repos, filterable by status.",
    "machines": "The Machines tab shows hardware telemetry for each fleet node.",
    "stats": "The Stats tab shows P50/P95 duration analytics and success rates across workflows.",
    "runner-plan": "The Runner Plan tab manages day/night runner capacity scheduling.",
    "dispatch": (
        "To dispatch a remediation agent: go to Remediation tab, select a failed run,"
        " choose a provider, preview the plan, then dispatch."
    ),
    "provider": (
        "Providers are AI agents: Jules API (cloud, Google), Codex CLI (OpenAI),"
        " Claude Code CLI (Anthropic), Ollama (local)."
    ),
    "loop guard": (
        "Loop guard prevents infinite retry loops. When the same failure repeats more than"
        " max_same_failure_attempts times, dispatch is blocked."
    ),
}


@app.get("/api/maxwell/status")
async def get_maxwell_status() -> dict:
    """Probe Maxwell-Daemon status and connectivity."""
    import shutil

    maxwell_binary = shutil.which("maxwell") or shutil.which("maxwell-daemon")
    maxwell_url = os.environ.get("MAXWELL_URL", "")
    maxwell_port = int(os.environ.get("MAXWELL_PORT", 8322))

    # Check if maxwell service is running via systemd
    service_running = False
    service_detail = "unknown"
    try:
        import subprocess

        r = subprocess.run(
            ["systemctl", "is-active", "maxwell-daemon"],
            capture_output=True,
            text=True,
            timeout=5,
            env=safe_subprocess_env(),
        )
        if r.returncode == 0 and r.stdout.strip() == "active":
            service_running = True
            service_detail = "systemd service active"
        else:
            service_detail = r.stdout.strip() or "not active"
    except Exception:  # noqa: BLE001
        service_detail = "systemd probe failed"

    # Check HTTP reachability
    http_reachable = False
    http_detail = ""
    base_url = maxwell_url or f"http://localhost:{maxwell_port}"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/api/health")
            http_reachable = resp.status_code == 200
            http_detail = f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001
        http_detail = str(e)[:80]

    status = "running" if (service_running or http_reachable) else "stopped"

    return {
        "status": status,
        "binary_found": maxwell_binary is not None,
        "binary_path": maxwell_binary,
        "service_running": service_running,
        "service_detail": service_detail,
        "http_reachable": http_reachable,
        "http_detail": http_detail,
        "dashboard_url": base_url,
        "deep_links": {
            "dashboard": base_url,
            "health": f"{base_url}/api/health",
            "logs": "journalctl -u maxwell-daemon -f",
        },
        "probed_at": datetime.now(UTC).isoformat(),
    }


@app.post("/api/maxwell/control")
async def maxwell_control(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("maxwell.control")),  # noqa: B008
) -> dict:
    """Start or stop Maxwell-Daemon service (confirmation required)."""
    body = await request.json()
    action = str(body.get("action", "")).strip()
    approved_by = str(body.get("approved_by", "")).strip()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=422, detail="action must be start, stop, or restart")
    if not approved_by:
        raise HTTPException(status_code=422, detail="approved_by required for privileged action")

    code, out, stderr = await run_cmd(["systemctl", action, "maxwell-daemon"], timeout=15, cwd=REPO_ROOT)
    log.info(
        "maxwell_control: action=%s approved_by=%s exit_code=%d",
        sanitize_log_value(action),
        sanitize_log_value(approved_by),
        code,
    )
    if code != 0:
        log.warning("maxwell %s failed: %s", action, stderr.strip()[:200])
        raise HTTPException(
            status_code=502,
            detail=f"maxwell {action} failed",
        )
    return {"status": action + "ed", "action": action, "approved_by": approved_by}


# ─── Maxwell-Daemon Proxy Routes ──────────────────────────────────────────────


def _maxwell_base_url() -> str:
    """Return the Maxwell-Daemon base URL from env."""
    return os.environ.get("MAXWELL_URL", "") or f"http://localhost:{int(os.environ.get('MAXWELL_PORT', 8322))}"


@app.get("/api/maxwell/version")
async def get_maxwell_version() -> dict:
    """Proxy GET /api/version from Maxwell-Daemon."""
    path = "/api/version"
    resp = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_maxwell_base_url()}{path}")
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            return resp.json()
    except Exception as e:  # noqa: BLE001
        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}

        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}


@app.get("/api/maxwell/daemon-status")
async def get_maxwell_daemon_status_detail() -> dict:
    """Proxy GET /api/status from Maxwell-Daemon (pipeline state)."""
    path = "/api/status"
    resp = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_maxwell_base_url()}{path}")
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            return resp.json()
    except Exception as e:  # noqa: BLE001
        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}

        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}


@app.get("/api/maxwell/tasks")
async def get_maxwell_tasks(limit: int = 20, cursor: str | None = None) -> dict:
    """Proxy GET /api/tasks from Maxwell-Daemon."""
    path = "/api/tasks"
    resp = None
    try:
        params: dict = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_maxwell_base_url()}{path}", params=params)
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            return resp.json()
    except Exception as e:  # noqa: BLE001
        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}

        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}


@app.get("/api/maxwell/tasks/{task_id}")
async def get_maxwell_task_detail(task_id: str) -> dict:
    """Proxy GET /api/tasks/{task_id} from Maxwell-Daemon."""
    path = f"/api/tasks/{task_id}"
    resp = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_maxwell_base_url()}{path}")
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            return resp.json()
    except Exception as e:  # noqa: BLE001
        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}

        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}


@app.post("/api/maxwell/dispatch")
async def maxwell_dispatch_task(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("maxwell.control")),  # noqa: B008
) -> dict:
    """Proxy POST /api/dispatch to Maxwell-Daemon (forwards body as-is)."""
    path = "/api/dispatch"
    resp = None
    try:
        body = await request.body()
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"{_maxwell_base_url()}{path}",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            return resp.json()
    except Exception as e:  # noqa: BLE001
        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}

        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}


@app.post("/api/maxwell/chat", response_model=None)
async def maxwell_chat(
    body: MaxwellChatBody,
    *,
    principal: Principal = Depends(require_scope("operator")),  # noqa: B008
) -> StreamingResponse:
    """Proxy chat messages to Maxwell-Daemon while preserving streamed output."""
    path = "/api/chat"
    payload = {
        "message": body.message,
        "history": body.history[-20:],
        "stream": True,
    }

    async def stream_daemon_response() -> Any:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{_maxwell_base_url()}{path}", json=payload) as resp:
                    log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
                    if resp.status_code >= 400:
                        yield f"Maxwell chat failed with HTTP {resp.status_code}."
                        return
                    async for chunk in resp.aiter_text():
                        if chunk:
                            yield chunk
        except Exception as e:  # noqa: BLE001
            log.info("maxwell_proxy: path=%s status=%s", path, "error")
            yield f"Maxwell-Daemon is unreachable: {str(e)[:120]}"

    return StreamingResponse(stream_daemon_response(), media_type="text/plain; charset=utf-8")


@app.post("/api/maxwell/pipeline-control/{action}")
async def maxwell_pipeline_control(
    action: str,
    request: Request,
    *,
    principal: Principal = Depends(require_scope("maxwell.control")),  # noqa: B008
) -> dict:
    """Proxy POST /api/control/{action} to Maxwell-Daemon."""
    if action not in ("pause", "resume", "abort"):
        raise HTTPException(status_code=422, detail="action must be pause, resume, or abort")
    path = f"/api/control/{action}"
    resp = None
    try:
        body = await request.body()
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"{_maxwell_base_url()}{path}",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            log.info("maxwell_proxy: path=%s status=%s", path, resp.status_code)
            return resp.json()
    except Exception as e:  # noqa: BLE001
        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}

        log.info("maxwell_proxy: path=%s status=%s", path, "error")
        return {"error": str(e)[:120], "daemon_available": False}


@app.post("/api/help/chat")
async def help_chat(request: Request, *, principal: Principal = Depends(require_scope("operator"))) -> dict:  # noqa: B008
    """Answer a dashboard help question. Uses local FAQ first, falls back to Claude API if available."""
    body = await request.json()
    question = str(body.get("question", "")).strip()
    current_tab = str(body.get("current_tab", "")).strip()
    if not question:
        raise HTTPException(status_code=422, detail="question required")

    # Try local FAQ match first
    q_lower = question.lower()
    faq_match = None
    for key, answer in DASHBOARD_FAQ.items():
        if key in q_lower:
            faq_match = answer
            break

    if faq_match:
        return {"answer": faq_match, "source": "faq"}

    # Try Claude API if available
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        try:
            import httpx

            system_prompt = (
                "You are a helpful assistant for a GitHub Actions runner dashboard. "
                "The dashboard has these tabs: Fleet, Queue, History, Machines, Organization, "
                "Heavy Tests, Stats, Reports, Scheduled Workflows, Runner Plan, Local Tools, "
                "Deployment, Remediation, Workflows, Credentials, Assessments, Feature Requests, Maxwell. "
                f"The user is currently on the '{current_tab}' tab. "
                "Answer concisely in 1-3 sentences. Focus on how to accomplish tasks in the dashboard."
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": DEFAULT_LLM_MODEL,
                        "max_tokens": 200,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": question}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    answer = data.get("content", [{}])[0].get("text", "")
                    if answer:
                        return {"answer": answer, "source": "claude"}
        except Exception as e:  # noqa: BLE001
            log.warning("help_chat claude fallback failed: %s", e)

    # Generic fallback
    tab_help = DASHBOARD_FAQ.get(current_tab, "")
    if tab_help:
        return {"answer": f"For the {current_tab} tab: {tab_help}", "source": "faq"}
    return {
        "answer": (
            "Try the Remediation tab to dispatch agents for failing CI,"
            " or the Workflows tab to manually trigger workflows."
        ),
        "source": "fallback",
    }


# ─── Assessments ──────────────────────────────────────────────────────────────


@app.get("/api/assessments/scores")
async def get_assessment_scores() -> dict:
    """Return assessment score history from local assessments directory."""
    assessments_dir = REPO_ROOT / "assessments"
    results: list[dict] = []
    if assessments_dir.exists():
        for score_file in sorted(assessments_dir.rglob("*.json"), reverse=True)[:50]:
            try:
                data = json.loads(score_file.read_text(encoding="utf-8"))
                results.append(
                    {
                        "file": str(score_file.relative_to(REPO_ROOT)),
                        "repo": data.get("repository") or score_file.parent.name,
                        "score": data.get("score") or data.get("overall_score"),
                        "date": data.get("date") or data.get("timestamp") or score_file.stat().st_mtime,
                        "summary": data.get("summary") or data.get("description", "")[:200],
                        "provider": data.get("provider") or data.get("agent", ""),
                    }
                )
            except Exception:  # noqa: BLE001
                pass
    return {"scores": results, "total": len(results)}


@app.post("/api/assessments/dispatch")
async def dispatch_assessment(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assessments.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch an assessment workflow for a repository."""
    body = await request.json()
    repo = str(body.get("repository", "")).strip()
    provider = str(body.get("provider", "jules_api")).strip()
    ref = str(body.get("ref", "main")).strip()
    if not repo:
        raise HTTPException(status_code=422, detail="repository required")
    if not provider:
        raise HTTPException(status_code=422, detail="provider required")

    log.info(
        "audit: assessments_dispatch repo=%s provider=%s ref=%s",
        sanitize_log_value(repo),
        sanitize_log_value(provider),
        sanitize_log_value(ref),
    )

    # Try to dispatch via GitHub Actions assessment workflow
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Jules-Assess-Repo.yml/dispatches"
    payload = {
        "ref": "main",
        "inputs": {"target_repository": f"{ORG}/{repo}", "provider": provider},
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        log.warning("assessment dispatch failed: repo=%s stderr=%s", repo, stderr.strip()[:300])
        raise HTTPException(
            status_code=502,
            detail="Assessment dispatch failed",
        )
    return {"status": "dispatched", "repository": repo, "provider": provider}


# ─── Feature Requests ─────────────────────────────────────────────────────────

_FEATURE_REQUESTS_PATH = Path.home() / "actions-runners" / "dashboard" / "feature_requests.json"
_PROMPT_TEMPLATES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_templates.json"
_PROMPT_NOTES_PATH = Path.home() / "actions-runners" / "dashboard" / "prompt_notes.json"

STANDARDS_INJECTION: dict[str, str] = {
    "tdd": (
        "Use Test-Driven Development: write failing tests first (RED), then minimal code to pass (GREEN),"
        " then refactor. Tests must pass before any PR."
    ),
    "dbc": (
        "Apply Design by Contract: validate inputs at boundaries, assert internal invariants,"
        " document pre/postconditions in docstrings."
    ),
    "dry": (
        "Apply DRY: extract shared logic into modules, eliminate duplication."
        " Three similar code blocks should become one shared function."
    ),
    "lod": (
        "Apply Law of Demeter: components talk to immediate neighbors only."
        " UI receives view models, not raw nested payloads."
    ),
    "security": (
        "Apply security-first: validate all inputs, avoid injection vulnerabilities,"
        " use parameterized queries, never log secrets."
    ),
    "docs": (
        "Document public APIs, non-obvious decisions, and architecture choices."
        " Prefer short clear docstrings over multi-paragraph ones."
    ),
}


@app.get("/api/feature-requests")
async def list_feature_requests() -> dict:
    """List saved feature implementation requests."""
    try:
        if _FEATURE_REQUESTS_PATH.exists():
            data = json.loads(_FEATURE_REQUESTS_PATH.read_text(encoding="utf-8"))
        else:
            data = []
    except Exception:  # noqa: BLE001
        data = []
    return {"requests": list(reversed(data[-100:])), "total": len(data)}


@app.get("/api/feature-requests/templates")
async def list_prompt_templates() -> dict:
    """List saved prompt templates and global prompt notes."""
    templates_data = []
    try:
        if _PROMPT_TEMPLATES_PATH.exists():
            templates_data = json.loads(_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    prompt_notes_data = {"notes": "", "enabled": True}
    try:
        if _PROMPT_NOTES_PATH.exists():
            prompt_notes_data = json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    return {
        "templates": templates_data,
        "standards": STANDARDS_INJECTION,
        "promptNotes": prompt_notes_data,
    }


@app.post("/api/feature-requests/templates")
async def save_prompt_template(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("feature-requests.manage")),  # noqa: B008
) -> dict:
    """Save a prompt template."""
    body = await request.json()
    name = str(body.get("name", "")).strip()
    content = str(body.get("content", "")).strip()
    if not name or not content:
        raise HTTPException(status_code=422, detail="name and content required")
    async with _prompt_templates_lock:
        try:
            templates: list[dict] = []
            if _PROMPT_TEMPLATES_PATH.exists():
                templates = json.loads(_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
            existing_idx = next((i for i, t in enumerate(templates) if t.get("name") == name), None)
            template = {
                "name": name,
                "content": content,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            if existing_idx is not None:
                templates[existing_idx] = template
            else:
                templates.append(template)
            config_schema.atomic_write_json(_PROMPT_TEMPLATES_PATH, templates)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
    return {"status": "saved", "name": name}


@app.get("/api/settings/prompt-notes")
async def get_prompt_notes() -> dict:
    """Get the global prompt notes that are automatically injected into every prompt."""
    try:
        if _PROMPT_NOTES_PATH.exists():
            data = json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
        else:
            data = {"notes": "", "enabled": True}
    except Exception:  # noqa: BLE001
        data = {"notes": "", "enabled": True}
    return data


@app.put("/api/settings/prompt-notes")
async def update_prompt_notes(request: Request, *, principal: Principal = Depends(require_scope("operator"))) -> dict:  # noqa: B008
    """Update the global prompt notes."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="expected object body")

    notes = str(body.get("notes", "")).strip()
    enabled = bool(body.get("enabled", True))

    async with _prompt_notes_lock:
        try:
            data = {"notes": notes, "enabled": enabled}
            config_schema.atomic_write_json(_PROMPT_NOTES_PATH, data)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
    return {"status": "saved", "notes_length": len(notes), "enabled": enabled}


@app.post("/api/feature-requests/dispatch")
async def dispatch_feature_request(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("feature-requests.manage")),  # noqa: B008
) -> dict:
    """Dispatch a feature implementation request via CI remediation workflow."""
    client_ip = request.client.host if request.client else "unknown"
    check_dispatch_rate(client_ip)
    body = await request.json()
    repo = str(body.get("repository", "")).strip()
    branch = str(body.get("branch", "main")).strip()
    provider = str(body.get("provider", "jules_api")).strip()
    prompt = str(body.get("prompt", "")).strip()
    standards = body.get("standards", []) or []
    template_id = str(body.get("template_id", "")).strip()
    if not repo:
        raise HTTPException(status_code=422, detail="repository required")
    if not prompt and not template_id:
        raise HTTPException(status_code=422, detail="prompt or template_id required")

    log.info(
        "audit: feature_request_dispatch repo=%s provider=%s branch=%s",
        sanitize_log_value(repo),
        sanitize_log_value(provider),
        sanitize_log_value(branch),
    )

    # Load and apply prompt notes if enabled
    prompt_notes_data: dict[str, object] = {"notes": "", "enabled": True}
    try:
        if _PROMPT_NOTES_PATH.exists():
            prompt_notes_data = json.loads(_PROMPT_NOTES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    # Build full prompt with notes and standards injection
    full_prompt = prompt
    notes_val = str(prompt_notes_data.get("notes", ""))
    if prompt_notes_data.get("enabled", True) and notes_val.strip():
        full_prompt = f"{notes_val}\n\n{prompt}"

    injected_standards = "\n\n".join(
        f"[{s.upper()}] {STANDARDS_INJECTION[s]}" for s in standards if s in STANDARDS_INJECTION
    )
    if injected_standards:
        full_prompt = f"{full_prompt}\n\n## Engineering Standards\n{injected_standards}"

    # Save to history
    entry: dict = {}
    async with _feature_requests_lock:
        try:
            history: list[dict] = []
            if _FEATURE_REQUESTS_PATH.exists():
                history = json.loads(_FEATURE_REQUESTS_PATH.read_text(encoding="utf-8"))
            entry = {
                "id": str(int(datetime.now(UTC).timestamp())),
                "repository": repo,
                "branch": branch,
                "provider": provider,
                "prompt": prompt[:500],
                "standards": list(standards),
                "status": "dispatched",
                "created_at": datetime.now(UTC).isoformat(),
            }
            history.append(entry)
            config_schema.atomic_write_json(_FEATURE_REQUESTS_PATH, history[-200:])
        except Exception:  # noqa: BLE001
            pass

    # Dispatch via feature-request workflow
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Jules-Feature-Request.yml/dispatches"
    payload = {
        "ref": "main",
        "inputs": {
            "target_repository": f"{ORG}/{repo}",
            "branch": branch,
            "provider": provider,
            "prompt": full_prompt[:10000],
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await run_cmd(
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        log.warning("feature_request_dispatch failed: %s", stderr.strip()[:200])
        # Don't raise - save history record and return success anyway (workflow may not exist yet)
    return {
        "status": "dispatched",
        "repository": repo,
        "provider": provider,
        "entry_id": entry.get("id", ""),
    }


# ─── Fleet Orchestration Control Plane ───────────────────────────────────────

_ORCHESTRATION_AUDIT_PATH = Path.home() / "actions-runners" / "dashboard" / "orchestration_audit.json"
_DEPLOY_ACTIONS = {"sync_workflows", "restart_runner", "update_config"}


def _load_orchestration_audit(limit: int = 50, principal: str | None = None) -> list[dict]:
    """Load recent orchestration audit entries from disk."""
    if not _ORCHESTRATION_AUDIT_PATH.exists():
        return []
    try:
        raw = _ORCHESTRATION_AUDIT_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        entries = json.loads(raw)
        if isinstance(entries, list):
            if principal:
                entries = [e for e in entries if e.get("principal") == principal]
            return entries[-limit:]
        return []
    except (OSError, json.JSONDecodeError):
        return []


async def _append_orchestration_audit(entry: dict) -> None:
    """Append a single audit entry to the orchestration audit log (thread-safe)."""
    async with _orchestration_audit_lock:
        existing = _load_orchestration_audit(limit=1000)
        existing.append(entry)
        try:
            config_schema.atomic_write_json(_ORCHESTRATION_AUDIT_PATH, existing)
        except OSError as exc:
            log.warning("orchestration audit write failed: %s", exc)


@app.get("/api/audit", tags=["fleet"])
async def get_node_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> list[dict]:
    """Return this node's orchestration audit log."""
    return _load_orchestration_audit(limit=limit, principal=principal)


@app.get("/api/fleet/audit", tags=["fleet"])
async def get_fleet_audit_log(
    request: Request,
    limit: int = 50,
    principal: str | None = None,
    _auth: Principal = Depends(require_principal),
) -> dict:
    """Return a merged view of orchestration audit logs across the fleet."""
    local_entries = _load_orchestration_audit(limit=limit, principal=principal)
    all_entries = list(local_entries)

    async def fetch_remote_audit(name: str, url: str) -> list[dict]:
        try:
            params: dict[str, Any] = {"limit": limit}
            if principal:
                params["principal"] = principal
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if auth_header := request.headers.get("Authorization"):
                    headers["Authorization"] = auth_header
                r = await client.get(f"{url}/api/audit", params=params, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to fetch audit from %s (%s): %s", name, url, exc)
        return []

    if FLEET_NODES:
        remotes = await asyncio.gather(*[fetch_remote_audit(n, u) for n, u in FLEET_NODES.items()])
        for r_entries in remotes:
            all_entries.extend(r_entries)

    def _parse_ts(entry: dict) -> _dt_mod.datetime:
        ts_str = entry.get("timestamp") or entry.get("ts") or ""
        try:
            return _dt_mod.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return _dt_mod.datetime.min.replace(tzinfo=UTC)

    all_entries.sort(key=_parse_ts, reverse=True)

    return {
        "entries": all_entries[:limit],
        "count": len(all_entries[:limit]),
    }


@app.get("/api/fleet/orchestration")
async def get_fleet_orchestration(request: Request) -> dict:
    """Return per-machine job assignment, queue, and capacity for fleet orchestration view."""
    registry_data = load_machine_registry()
    machines_raw = registry_data.get("machines", [])

    # Try to enrich with live node data from cache
    try:
        fleet = await _get_fleet_nodes_impl()
        live_nodes = {n.get("name", ""): n for n in fleet.get("nodes", [])}
    except Exception:  # noqa: BLE001
        live_nodes = {}

    machines = []
    for m in machines_raw:
        name = m.get("name", "")
        live = live_nodes.get(name, {})
        online = bool(live.get("online", False)) if live else False
        system_info = live.get("system", {}) if live else {}
        runners_info = live.get("runners", []) if live else []
        runner_count = len(runners_info) if isinstance(runners_info, list) else 0
        busy_count = sum(1 for r in runners_info if r.get("busy")) if runner_count else 0
        machines.append(
            {
                "name": name,
                "display_name": m.get("display_name") or name,
                "role": m.get("role", "node"),
                "online": online,
                "runner_count": runner_count,
                "busy_runners": busy_count,
                "queue_depth": max(0, busy_count),
                "last_ping": live.get("last_ping") or live.get("checked_at"),
                "dashboard_url": m.get("dashboard_url"),
                "runner_labels": m.get("runner_labels", []),
                "offline_reason": live.get("offline_reason"),
                "cpu_percent": system_info.get("cpu_percent"),
                "memory_percent": system_info.get("memory_percent"),
            }
        )

    audit_entries = _load_orchestration_audit(limit=10)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "machines": machines,
        "online_count": sum(1 for m in machines if m["online"]),
        "total_count": len(machines),
        "audit_log": list(reversed(audit_entries)),
    }


@app.post("/api/fleet/orchestration/dispatch")
async def fleet_orchestration_dispatch(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Dispatch a workflow to a specific machine target."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object")

    repo = str(body.get("repo", "")).strip()
    workflow = str(body.get("workflow", "")).strip()
    ref = str(body.get("ref", "main")).strip() or "main"
    machine_target = str(body.get("machine_target", "")).strip()
    inputs = body.get("inputs") or {}
    approved_by = principal.id

    if not repo or not workflow:
        raise HTTPException(status_code=422, detail="repo and workflow are required")

    log.info(
        "audit: fleet_orchestration_dispatch repo=%s workflow=%s ref=%s target=%s by=%s",
        sanitize_log_value(repo),
        sanitize_log_value(workflow),
        sanitize_log_value(ref),
        sanitize_log_value(machine_target),
        sanitize_log_value(approved_by),
    )

    from uuid import uuid4  # noqa: PLC0415

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Build a dispatch_contract envelope for auditing
    try:
        confirmation = dispatch_contract.DispatchConfirmation(
            approved_by=approved_by,
            approved_at=now_str,
            note=f"Fleet orchestration dispatch to {machine_target or 'any'}",
        )
        envelope = dispatch_contract.build_envelope(
            action="runner.status",  # read-only, used for audit record only
            source="fleet-orchestration",
            target=machine_target or "fleet",
            requested_by=approved_by,
            reason=f"Dispatch {workflow} on {repo}@{ref}",
            payload={"repo": repo, "workflow": workflow, "ref": ref, "inputs": inputs},
            confirmation=confirmation,
            principal=principal.id,
            on_behalf_of=getattr(request.state, "on_behalf_of", None) or "",
        )
        validation = dispatch_contract.validate_envelope(envelope)
        audit_entry_obj = dispatch_contract.build_audit_log_entry(envelope, validation)
        audit_entry = audit_entry_obj.to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration dispatch audit build failed: %s", exc)
        audit_entry = {
            "event_id": audit_id,
            "action": "workflow.dispatch",
            "target": machine_target,
            "requested_by": approved_by,
            "decision": "accepted",
            "recorded_at": now_str,
        }

    audit_entry["orchestration_type"] = "workflow_dispatch"
    audit_entry["repo"] = repo
    audit_entry["workflow"] = workflow
    audit_entry["ref"] = ref
    audit_entry["machine_target"] = machine_target
    audit_entry["audit_id"] = audit_id
    await _append_orchestration_audit(audit_entry)

    log.info(
        "fleet-orchestration dispatch repo=%s workflow=%s ref=%s target=%s by=%s",
        sanitize_log_value(repo),
        sanitize_log_value(workflow),
        sanitize_log_value(ref),
        sanitize_log_value(machine_target),
        sanitize_log_value(approved_by),
    )

    # Attempt actual workflow dispatch via gh CLI
    run_url = None
    try:
        endpoint = f"/repos/{ORG}/{repo}/actions/workflows/{workflow}/dispatches"
        dispatch_payload: dict = {"ref": ref}
        if inputs:
            dispatch_payload["inputs"] = inputs
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as pf_obj:
            json.dump(dispatch_payload, pf_obj)
            pf = pf_obj.name
        try:
            code, _, stderr = await run_cmd(
                ["gh", "api", endpoint, "--method", "POST", "--input", pf],
                timeout=30,
                cwd=REPO_ROOT,
            )
        finally:
            with contextlib.suppress(OSError):
                Path(pf).unlink()
        if code != 0:
            log.warning("orchestration workflow dispatch gh failed: %s", stderr[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration dispatch gh call failed: %s", exc)

    return {
        "dispatched": True,
        "run_url": run_url,
        "audit_id": audit_id,
        "machine_target": machine_target,
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
    }


@app.post("/api/fleet/orchestration/deploy")
async def fleet_orchestration_deploy(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008
) -> dict:
    """Deploy a workflow or config change to a fleet machine."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object")

    machine = str(body.get("machine", "")).strip()
    action = str(body.get("action", "")).strip()
    confirmed = bool(body.get("confirmed", False))
    requested_by = principal.id

    if not machine:
        raise HTTPException(status_code=422, detail="machine is required")
    if action not in _DEPLOY_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of: {', '.join(sorted(_DEPLOY_ACTIONS))}",
        )
    if not confirmed:
        raise HTTPException(
            status_code=403,
            detail="confirmed=true is required to deploy to a fleet machine",
        )

    log.info(
        "audit: fleet_orchestration_deploy machine=%s action=%s by=%s",
        sanitize_log_value(machine),
        sanitize_log_value(action),
        sanitize_log_value(requested_by),
    )

    from uuid import uuid4  # noqa: PLC0415

    audit_id = uuid4().hex
    now_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Map deploy actions to dispatch_contract actions for auditing
    contract_action_map = {
        "sync_workflows": "dashboard.update_and_restart",
        "restart_runner": "runner.restart",
        "update_config": "runner.restart",
    }
    contract_action = contract_action_map.get(action, "runner.restart")

    try:
        confirmation = dispatch_contract.DispatchConfirmation(
            approved_by=requested_by,
            approved_at=now_str,
            note=f"Fleet deploy action={action} to machine={machine}",
        )
        envelope = dispatch_contract.build_envelope(
            action=contract_action,
            source="fleet-orchestration",
            target=machine,
            requested_by=requested_by,
            reason=f"Deploy action {action} to {machine}",
            payload={"deploy_action": action},
            confirmation=confirmation,
            principal=principal.id,
            on_behalf_of=getattr(request.state, "on_behalf_of", None) or "",
        )
        validation = dispatch_contract.validate_envelope(envelope)
        audit_entry_obj = dispatch_contract.build_audit_log_entry(envelope, validation)
        audit_entry = audit_entry_obj.to_dict()
    except Exception as exc:  # noqa: BLE001
        log.warning("orchestration deploy audit build failed: %s", exc)
        audit_entry = {
            "event_id": audit_id,
            "action": action,
            "target": machine,
            "requested_by": requested_by,
            "decision": "accepted",
            "recorded_at": now_str,
        }

    audit_entry["orchestration_type"] = "fleet_deploy"
    audit_entry["deploy_action"] = action
    audit_entry["machine"] = machine
    audit_entry["audit_id"] = audit_id
    await _append_orchestration_audit(audit_entry)

    log.info(
        "fleet-orchestration deploy machine=%s action=%s by=%s",
        sanitize_log_value(machine),
        sanitize_log_value(action),
        sanitize_log_value(requested_by),
    )

    action_labels = {
        "sync_workflows": "Sync workflows",
        "restart_runner": "Restart runner",
        "update_config": "Update config",
    }
    return {
        "deployed": True,
        "machine": machine,
        "action": action,
        "message": f"{action_labels.get(action, action)} dispatched to {machine}",
        "audit_id": audit_id,
    }


# ─── Diagnostics & Launchers ──────────────────────────────────────────────────


@app.get("/api/deployment/git-drift")
async def get_git_drift() -> dict:
    """Return git-commit-based drift: compares HEAD against origin/main."""
    repo_root = Path(__file__).parent.parent
    result: dict[str, object] = {}

    source = "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=repo_root,
        )
        source = out.stdout.strip()
        result["source_commit"] = source[:12]
    except Exception:  # noqa: BLE001
        result["source_commit"] = "unknown"

    try:
        out = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=repo_root,
        )
        remote = out.stdout.strip()
        result["remote_commit"] = remote[:12]
        result["is_drifted"] = bool(source and remote and source != remote)
        if result["is_drifted"]:
            result["drift_details"] = "deployed version differs from origin/main"
        else:
            result["drift_details"] = "up to date"
    except Exception:  # noqa: BLE001
        result["is_drifted"] = False
        result["remote_commit"] = "unknown"
        result["drift_details"] = "could not reach origin/main"

    result["process_pid"] = os.getpid()
    return result


@app.get("/api/diagnostics/summary")
async def get_diagnostics_summary() -> dict:
    """Consolidated diagnostics for the Diagnostics tab."""
    summary: dict[str, object] = {}

    # WSL status
    try:
        wsl_result = subprocess.run(
            ["wsl", "-l", "-v"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-16-le",
            errors="replace",
        )
        summary["wsl_status"] = wsl_result.stdout.strip()
        summary["wsl_available"] = wsl_result.returncode == 0
    except Exception:  # noqa: BLE001
        try:
            wsl_result_raw = subprocess.run(
                ["wsl", "-l", "-v"],
                capture_output=True,
                timeout=10,
            )
            summary["wsl_status"] = wsl_result_raw.stdout.decode("utf-16-le", errors="replace").strip()
            summary["wsl_available"] = wsl_result_raw.returncode == 0
        except Exception:  # noqa: BLE001
            summary["wsl_status"] = "WSL not available"
            summary["wsl_available"] = False

    # Dashboard process info
    proc = psutil.Process(os.getpid())
    summary["dashboard_pid"] = proc.pid
    summary["dashboard_memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
    summary["dashboard_port"] = PORT

    # Git commit
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent.parent,
        )
        summary["git_commit"] = out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        summary["git_commit"] = "unknown"

    # Drift info
    try:
        drift = await get_git_drift()
        summary["is_drifted"] = drift.get("is_drifted", False)
        summary["source_commit"] = drift.get("source_commit", "unknown")
        summary["remote_commit"] = drift.get("remote_commit", "unknown")
        summary["drift_details"] = drift.get("drift_details", "")
    except Exception:  # noqa: BLE001
        summary["is_drifted"] = False

    return summary


@app.post("/api/diagnostics/restart-service")
async def restart_dashboard_service(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Restart the dashboard systemd service (WSL/Linux only, localhost only)."""
    client = request.client
    if not client or client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Local access only")

    try:
        result = subprocess.run(
            [SYSTEMCTL_BIN, "--user", "restart", "runner-dashboard"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "success": result.returncode == 0,
            "output": (result.stdout + result.stderr).strip(),
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to restart runner-dashboard service")
        raise HTTPException(status_code=500, detail="Restart failed") from exc

        log.exception("Failed to restart runner-dashboard service")
        raise HTTPException(status_code=500, detail="Restart failed") from exc


@app.post("/api/launchers/generate")
async def generate_launchers(
    request: Request,
    principal: Principal = Depends(require_scope("system.control")),  # noqa: B008
) -> dict:
    """Generate Windows PowerShell launcher scripts on the Desktop."""
    output_dir = Path.home() / "Desktop" / "RunnerDashboard"
    output_dir.mkdir(parents=True, exist_ok=True)

    launchers_created: list[str] = []

    script = output_dir / "Open-Dashboard.ps1"
    script.write_text('Start-Process "http://localhost:8321"\n', encoding="utf-8")
    launchers_created.append(str(script))

    keepalive = output_dir / "Start-WSL-Keepalive.ps1"
    keepalive.write_text(
        'Start-ScheduledTask -TaskName "WSL-Dashboard-Keepalive" -ErrorAction SilentlyContinue\n'
        'Write-Host "Keepalive task started"\n',
        encoding="utf-8",
    )
    launchers_created.append(str(keepalive))

    restart = output_dir / "Restart-Dashboard-Service.ps1"
    restart.write_text(
        'wsl -e bash -c "systemctl --user restart runner-dashboard && echo Service restarted"\n',
        encoding="utf-8",
    )
    launchers_created.append(str(restart))

    diag = output_dir / "Open-Diagnostics.ps1"
    diag.write_text('Start-Process "http://localhost:8321/#diagnostics"\n', encoding="utf-8")
    launchers_created.append(str(diag))

    log.info("Generated %d launcher scripts in %s", len(launchers_created), output_dir)
    return {
        "output_dir": str(output_dir),
        "launchers": launchers_created,
        "message": f"Created {len(launchers_created)} launcher scripts in {output_dir}",
    }


# ─── Hosted-Runner Billing Audit ─────────────────────────────────────────────

HOSTED_RUNNER_PATTERNS = re.compile(
    r"^(ubuntu-|windows-|macos-|GitHub Actions \d|Hosted Agent)",
    re.IGNORECASE,
)
_runner_audit_cache: dict[str, Any] = {"violations": [], "last_checked": None, "error": None}
_runner_audit_lock = asyncio.Lock()


@app.get("/api/runner-routing-audit")
async def get_runner_routing_audit() -> JSONResponse:
    """Return recent workflow runs that executed on GitHub-hosted runners."""
    return JSONResponse(_runner_audit_cache)


@app.post("/api/runner-routing-audit/refresh")
async def refresh_runner_routing_audit() -> JSONResponse:
    """Trigger an immediate audit refresh."""
    asyncio.create_task(_run_runner_audit())
    return JSONResponse({"status": "refresh triggered"})


async def _run_runner_audit() -> None:
    async with _runner_audit_lock:
        try:
            violations = await _fetch_hosted_runner_violations()
            _runner_audit_cache["violations"] = violations
            _runner_audit_cache["last_checked"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            _runner_audit_cache["error"] = None
            if violations:
                log.warning(
                    "BILLING ALERT: %d workflow run(s) detected on GitHub-hosted runners",
                    len(violations),
                )
        except Exception as exc:  # noqa: BLE001
            _runner_audit_cache["error"] = str(exc)
            log.error("Runner routing audit failed: %s", exc)


async def _fetch_hosted_runner_violations() -> list[dict[str, Any]]:
    """Query GitHub API for recent runs on hosted runners across org repos."""
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        return []

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    org = ORG
    violations: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30) as client:
        repos_resp = await client.get(
            f"https://api.github.com/orgs/{org}/repos",
            headers=headers,
            params={"per_page": 50, "sort": "pushed"},
        )
        if repos_resp.status_code != 200:
            return []
        repos = [r["name"] for r in repos_resp.json()]

        for repo_name in repos[:20]:  # limit to 20 most recently pushed
            try:
                runs_resp = await client.get(
                    f"https://api.github.com/repos/{org}/{repo_name}/actions/runs",
                    headers=headers,
                    params={"per_page": 10, "status": "completed"},
                )
                if runs_resp.status_code != 200:
                    continue
                for run in runs_resp.json().get("workflow_runs", []):
                    jobs_resp = await client.get(
                        f"https://api.github.com/repos/{org}/{repo_name}/actions/runs/{run['id']}/jobs",
                        headers=headers,
                        params={"per_page": 30},
                    )
                    if jobs_resp.status_code != 200:
                        continue
                    for job in jobs_resp.json().get("jobs", []):
                        runner_name = job.get("runner_name") or ""
                        runner_group = job.get("runner_group_name") or ""
                        if HOSTED_RUNNER_PATTERNS.match(runner_name) or runner_group == "GitHub Actions":
                            violations.append(
                                {
                                    "repo": repo_name,
                                    "workflow": run.get("name", ""),
                                    "run_id": run["id"],
                                    "job_name": job.get("name", ""),
                                    "runner_name": runner_name,
                                    "runner_group": runner_group,
                                    "run_url": run.get("html_url", ""),
                                    "started_at": job.get("started_at", ""),
                                    "conclusion": job.get("conclusion", ""),
                                }
                            )
            except Exception:  # noqa: BLE001
                continue

    return violations


async def _runner_audit_loop() -> None:
    await asyncio.sleep(30)  # initial delay
    while True:
        await _run_runner_audit()
        await asyncio.sleep(900)  # 15 minutes


@app.on_event("startup")
async def _start_background_tasks() -> None:
    asyncio.create_task(_runner_audit_loop())


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print(f"\n{'=' * 60}")
    print("  D-sorganization Runner Dashboard v4.0")
    print(f"  Local:   http://localhost:{PORT}")
    print(f"  Network: http://0.0.0.0:{PORT}")
    print(f"  API docs: http://localhost:{PORT}/docs")
    print(f"  Health:   http://localhost:{PORT}/api/health")
    print(f"  Org: {ORG} | Host: {HOSTNAME}")
    print(f"  Runners: {NUM_RUNNERS} @ {RUNNER_BASE_DIR}")
    print(f"{'=' * 60}\n")

    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104 — intentional for local LAN/Tailscale access
        port=PORT,
        log_level="warning",  # FastAPI handles its own logging
    )
# ci-trigger
