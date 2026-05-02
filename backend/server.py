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
import datetime as _dt_mod
import errno
import json
import logging
import logging.handlers
import os
import platform
import random
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

# systemd watchdog / ready notification (issue #391 AC-3)
try:
    from systemd.daemon import notify as _sd_notify
except ImportError:
    _sd_notify = None

import httpx
import psutil
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
)
from fastapi.staticfiles import StaticFiles
from identity import Principal, require_scope  # noqa: B008
from middleware import MaxBodySizeMiddleware
from pydantic import BaseModel, Field
from routers import admin as admin_router
from routers import auth as auth_router
from starlette.middleware.sessions import SessionMiddleware

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import agent_dispatch_router as agent_dispatch_router  # noqa: E402
import agent_remediation as agent_remediation  # noqa: E402
import auth_webauthn as _auth_webauthn_router  # noqa: E402
import config_schema as config_schema  # noqa: E402
import dashboard_config as dashboard_config  # noqa: E402
import deployment_drift as deployment_drift  # noqa: E402
import dispatch_contract as dispatch_contract  # noqa: E402
import health as _health_router  # noqa: E402
import issue_inventory as issue_inventory  # noqa: E402
import lease_synchronizer as lease_synchronizer  # noqa: E402
import linear_inventory as linear_inventory  # noqa: E402
import metrics as _metrics_router  # noqa: E402
import orchestration_audit as orchestration_audit  # noqa: E402
import pr_inventory as pr_inventory  # noqa: E402
import prometheus_metrics as _prometheus_metrics_router  # noqa: E402
import push as _push_router  # noqa: E402
import quota_enforcement as quota_enforcement  # noqa: E402
import unified_issue_inventory as unified_issue_inventory  # noqa: E402
import usage_monitoring as usage_monitoring  # noqa: E402
from cache_utils import cache_delete as _cache_delete  # noqa: E402
from cache_utils import cache_get as _cache_get  # noqa: E402
from cache_utils import cache_set as _cache_set  # noqa: E402
from dashboard_config.cache_ttls import CacheTtl  # noqa: E402
from dashboard_config.timeouts import (  # noqa: E402
    Concurrency,
    HttpTimeout,
    ResourceThreshold,
)
from http_clients import initialize_http_clients, shutdown_http_clients  # noqa: E402
from local_app_monitoring import collect_local_apps  # noqa: E402
from machine_registry import (  # noqa: E402
    load_machine_registry,
    merge_registry_with_live_nodes,
)
from middleware import add_security_headers, csrf_check, max_body_size_check  # noqa: E402
from request_context import RequestIdMiddleware, configure_json_logging  # noqa: E402
from routers import assessments as _assessments_router  # noqa: E402

# parse_report_metrics and sanitize_report_date moved to routers/reports.py (issue #358)
from routers import assistant as _assistant_router  # noqa: E402
from routers import credentials as _credentials_router  # noqa: E402
from routers import deployment as _deployment_router  # noqa: E402
from routers import dispatch as _dispatch_router  # noqa: E402
from routers import feature_requests as _feature_requests_router  # noqa: E402
from routers import fleet as _fleet_router  # noqa: E402
from routers import heavy_tests as _heavy_tests_router  # noqa: E402
from routers import linear as _linear_router  # noqa: E402
from routers import linear_sync as _linear_sync_router  # noqa: E402  # issue #236
from routers import linear_webhook as _linear_webhook_router  # noqa: E402
from routers import maxwell as _maxwell_router  # noqa: E402
from routers import orchestration as _orchestration_router  # noqa: E402
from routers import queue as _queue_router  # noqa: E402
from routers import queue_diagnostics as _queue_diagnostics_router  # noqa: E402
from routers import remediation as _remediation_router  # noqa: E402
from routers import reports as _reports_router  # noqa: E402
from routers import runner_audit as _runner_audit_router  # noqa: E402  # issue #298
from routers import runner_diagnostics as _runner_diagnostics_router  # noqa: E402
from routers import runner_groups as _runner_groups_router  # noqa: E402
from routers import runners as _runners_router  # noqa: E402
from routers import runs_workflows as _runs_workflows_router  # noqa: E402
from routers import system as _system_router  # noqa: E402
from routers import web_vitals as _web_vitals_router  # noqa: E402
from routers import repos as _repos_router  # noqa: E402
from routers import diagnostics as _diagnostics_router  # noqa: E402
from routers.queue import _queue_impl  # noqa: E402
from security import (  # noqa: E402
    safe_subprocess_env,  # noqa: E402
    validate_fleet_node_url,  # noqa: E402
    validate_owner_repo_format,  # noqa: E402
    validate_repo_slug,  # noqa: E402
)
from system_utils import get_system_metrics_snapshot  # noqa: E402

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
# Configure JSON logging and request_id filter (issue #331).
# Must come after basicConfig so handlers are already attached.
configure_json_logging()

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


class HelpChatBody(BaseModel):
    question: str = Field(..., max_length=2000)
    current_tab: str = Field(default="", max_length=100)


# ─── Bounded Cache ────────────────────────────────────────────────────────────

MAX_CACHE_SIZE = 500
_CACHE_EVICT_BATCH = 50

# CPU history ring-buffer depth (one sample per /api/system poll; 60 ≈ 1 min at 1 Hz)
_CPU_HISTORY_MAXLEN = int(os.environ.get("DASHBOARD_CPU_HISTORY_MAXLEN", "60"))
CPU_HISTORY_MAXLEN = _CPU_HISTORY_MAXLEN

# ─── Shared State Locks ───────────────────────────────────────────────────────
_remediation_history_lock: asyncio.Lock = asyncio.Lock()
# _orchestration_audit_lock moved to orchestration_audit.py (issue #359).
# Feature-request locks moved to routers/feature_requests.py

# ─── Configuration ────────────────────────────────────────────────────────────
ORG = os.environ.get("GITHUB_ORG", "D-sorganization")
REPO_ROOT = Path(os.environ.get("RUNNER_DASHBOARD_REPO_ROOT", BACKEND_DIR.parents[1]))
RUNNER_BASE_DIR = Path.home() / "actions-runners"
DEFAULT_NUM_RUNNERS = 12
REQUESTED_NUM_RUNNERS = int(os.environ.get("NUM_RUNNERS", str(DEFAULT_NUM_RUNNERS)))
MAX_RUNNERS = int(os.environ.get("MAX_RUNNERS", str(REQUESTED_NUM_RUNNERS)))
NUM_RUNNERS = min(REQUESTED_NUM_RUNNERS, MAX_RUNNERS)
DISK_WARN_PERCENT = float(
    os.environ.get("DASHBOARD_DISK_WARN_PERCENT", str(ResourceThreshold.DISK_WARN_PERCENT)),
)
DISK_CRITICAL_PERCENT = float(
    os.environ.get("DASHBOARD_DISK_CRITICAL_PERCENT", str(ResourceThreshold.DISK_CRITICAL_PERCENT)),
)
DISK_MIN_FREE_GB = float(
    os.environ.get("DASHBOARD_DISK_MIN_FREE_GB", str(ResourceThreshold.DISK_MIN_FREE_GB)),
)
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

_cpu_history: deque[float] = deque(maxlen=_CPU_HISTORY_MAXLEN)


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
except (OSError, subprocess.SubprocessError, TimeoutError, ValueError):
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

# Issue #331 — request_id correlation middleware (outermost so all subsequent
# middleware and handlers have access to the request_id context var).
app.add_middleware(RequestIdMiddleware)

# Issue #350 — early body-size guard (ASGI middleware, runs before routing)
app.add_middleware(
    MaxBodySizeMiddleware,
    default_limit=1 * 1024 * 1024,  # 1 MB
)

# Issue #330 — Prometheus HTTP instrumentation middleware
app.add_middleware(_prometheus_metrics_router.PrometheusMiddleware)

# ── Replay-protection store (issue #344) ─────────────────────────────────────
# Replaces the unbounded JSON file with a bounded SQLite-backed store.
from replay_store import ReplayStore, migrate_json_to_sqlite  # noqa: E402

_PROCESSED_ENVELOPES_PATH = Path.home() / "actions-runners" / "dashboard" / "processed_envelopes.json"
_REPLAY_STORE_PATH = Path.home() / "actions-runners" / "dashboard" / "replay.db"
_replay_store: ReplayStore = ReplayStore(_REPLAY_STORE_PATH)

# One-shot migration: import any live entries from the legacy JSON file.
migrate_json_to_sqlite(_PROCESSED_ENVELOPES_PATH, _replay_store)


async def _is_envelope_replay(envelope_id: str) -> bool:
    """Check if envelope_id has already been processed (replay detection)."""
    return _replay_store.is_replay(envelope_id)


async def _record_processed_envelope(envelope_id: str, ttl_seconds: int = 86400) -> None:
    """Record that envelope_id has been processed (for replay detection)."""
    _replay_store.record(envelope_id)


async def _periodic_replay_purge() -> None:
    """Background task: purge expired replay-store entries every hour."""
    while True:
        await asyncio.sleep(3600)
        try:
            deleted = _replay_store.purge_expired()
            if deleted:
                log.info("replay_store: periodic purge removed %d entries", deleted)
        except Exception as exc:  # noqa: BLE001
            log.warning("replay_store: periodic purge failed: %s", exc)


# ── Bounded domain routers ────────────────────────────────────────────────────
app.include_router(_dispatch_router.router)
_dispatch_router.set_replay_functions(_is_envelope_replay, _record_processed_envelope)
app.include_router(_credentials_router.router)
app.include_router(_remediation_router.router)
app.include_router(_linear_router.router)
app.include_router(_linear_webhook_router.router)
app.include_router(_push_router.router)
app.include_router(admin_router.router)
app.include_router(auth_router.router)
app.include_router(_auth_webauthn_router.router)
app.include_router(_health_router.router)
app.include_router(_metrics_router.router)
app.include_router(_prometheus_metrics_router.router)

# Agent-launcher control surface (sibling: Repository_Management/launchers/cline_agent_launcher).
# Subprocess-only — never imports the launcher Python at runtime.
import agent_launcher_router as _agent_launcher_router  # noqa: E402

app.include_router(_agent_launcher_router.router)

# Batch-2 extracted routers (epic #159)
app.include_router(_system_router.router)
app.include_router(_web_vitals_router.router)
app.include_router(_fleet_router.router)
app.include_router(_queue_router.router)
app.include_router(_queue_diagnostics_router.router)
app.include_router(_runners_router.router)
app.include_router(_runner_groups_router.router)
app.include_router(_runner_diagnostics_router.router)
app.include_router(_runs_workflows_router.router)
app.include_router(_assistant_router.router)
app.include_router(_feature_requests_router.router)
app.include_router(_maxwell_router.router)
app.include_router(_deployment_router.router)
app.include_router(_reports_router.router)
app.include_router(_heavy_tests_router.router)
app.include_router(_assessments_router.router)
app.include_router(_orchestration_router.router)
app.include_router(_runner_audit_router.router)  # batch 3 extraction (issue #298)
app.include_router(_linear_sync_router.router)  # Linear read sync (issue #236)
app.include_router(_repos_router.router)  # issue #360
app.include_router(_diagnostics_router.router)  # issue #360

app.add_middleware(
    SessionMiddleware,
    secret_key=dashboard_config.SESSION_SECRET,
    session_cookie="dashboard_session",
    max_age=86400 * 7,  # 7 days
    same_site="strict",
    https_only=True,
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


@app.middleware("http")
async def _max_body_size(request, call_next):
    return await max_body_size_check(request, call_next)


@app.middleware("http")
async def _csrf_check(request, call_next):
    return await csrf_check(request, call_next)


@app.middleware("http")
async def _add_security_headers(request, call_next):
    return await add_security_headers(request, call_next)


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
    async with httpx.AsyncClient(timeout=HttpTimeout.PROXY_TO_HUB_S) as client:
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
            from proxy_utils import _translate_upstream_response

            return _translate_upstream_response(resp, "Hub proxy")
        except httpx.TimeoutException as e:
            log.warning("Hub proxy timeout for %s: %s", request.url.path, e)
            raise HTTPException(status_code=504, detail="Hub timeout") from e
        except httpx.ConnectError as e:
            log.warning("Hub proxy connect error for %s: %s", request.url.path, e)
            raise HTTPException(status_code=503, detail="Hub connection error") from e
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
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


async def run_cmd(
    cmd: list[str],
    timeout: int = HttpTimeout.GH_DISPATCH_S,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
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
        async with httpx.AsyncClient(timeout=HttpTimeout.HUB_VERSION_FETCH_S) as client:
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
    """Return (repo_name, full_name) for dashboard remediation inputs (issue #326).

    Validates against a strict regex before any owner comparison or subprocess
    interpolation to prevent SSRF via malformed owner/repo slugs.
    """
    text = str(value).strip()
    if "/" in text:
        # Validate full owner/repo format before extracting parts (issue #326)
        validate_owner_repo_format(text)
        owner, _, repo_name = text.partition("/")
        if owner.lower() != ORG.lower():
            raise HTTPException(status_code=422, detail=f"repository owner must be {ORG}")
        repo_name = validate_repo_slug(repo_name)
        return repo_name, f"{ORG}/{repo_name}"
    repo_name = validate_repo_slug(text)
    return repo_name, f"{ORG}/{repo_name}"


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
    """Classify why a fleet node is not fully reachable.

    Uses typed exception checks (httpx exception hierarchy and OSError.errno)
    rather than fragile substring matching on str(exc).
    """
    if status_code is not None:
        return {
            "offline_reason": "dashboard_unhealthy",
            "offline_detail": f"Dashboard returned HTTP {status_code}",
        }
    if isinstance(exc, httpx.TimeoutException):
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
        return {
            "offline_reason": "wsl_connection_lost",
            "offline_detail": "Dashboard port refused the connection.",
        }
    return {
        "offline_reason": "unknown",
        "offline_detail": str(exc) if exc else "Dashboard node is unreachable.",
    }


def _resource_offline_reason(system: dict) -> dict | None:
    """Return a resource-monitor reason when local metrics indicate throttling."""
    cpu = system.get("cpu") or {}
    memory = system.get("memory") or {}
    disk = system.get("disk") or {}
    pressure = []
    if (cpu.get("percent_1m_avg") or cpu.get("percent") or 0) >= ResourceThreshold.CPU_HARD_STOP_PERCENT:
        pressure.append(f"CPU >= {ResourceThreshold.CPU_HARD_STOP_PERCENT:g}%")
    if (memory.get("percent") or 0) >= ResourceThreshold.MEMORY_CRITICAL_PERCENT:
        pressure.append(f"memory >= {ResourceThreshold.MEMORY_CRITICAL_PERCENT:g}%")
    if (disk.get("pressure") or {}).get("status") == "critical":
        pressure.append("disk pressure critical")
    elif (disk.get("percent") or 0) >= ResourceThreshold.DISK_HARD_STOP_PERCENT:
        pressure.append(f"disk >= {ResourceThreshold.DISK_HARD_STOP_PERCENT:g}%")
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
        "@($task.Actions | ForEach-Object { [pscustomobject]@{ Execute = $_.Execute; Arguments = $_.Arguments } })"  # noqa: E501
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
    cached = _cache_get("watchdog", float(CacheTtl.WATCHDOG_S))
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


async def _collect_live_fleet_nodes() -> list[dict]:
    """Collect the live fleet node payload before registry metadata is merged."""

    async def fetch_node(name: str, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=HttpTimeout.PROXY_NODE_SYSTEM_S) as client:
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

    local_sys = await get_system_metrics_snapshot()
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


# Deployment routes extracted to routers/deployment.py and registered via app.include_router (issue #357).


@app.get("/api/local-apps")
async def get_local_apps(request: Request) -> dict:
    """Report local tool deployment, drift, service state, and health."""
    cached = _cache_get("local_apps", float(CacheTtl.LOCAL_APPS_S))
    if cached is not None:
        return cached

    data = await asyncio.to_thread(collect_local_apps)
    _cache_set("local_apps", data)
    return data


@app.get("/api/watchdog")
async def get_watchdog_status(request: Request):
    """Report the WSL keepalive and startup validation state."""
    return await _watchdog_status_impl()


# Runner routes extracted to routers/fleet.py and registered via app.include_router.
# Runs and workflow routes extracted to routers/runs_workflows.py and registered via app.include_router.


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


# Fleet control/{action}, /fleet/schedule, /fleet/capacity routes extracted to
# routers/orchestration.py (issue #359). _fleet_control_local and
# _remote_fleet_control are kept here and injected via set_dependencies().


# Repository routes extracted to routers/repos.py and registered via app.include_router (issue #360).


# ─── PR Inventory API ────────────────────────────────────────────────────────


# PR routes extracted to routers/repos.py (issue #360).

# PR detail route extracted to routers/repos.py (issue #360).

# ─── Issue Inventory API ──────────────────────────────────────────────────────


# Issue inventory routes extracted to routers/repos.py (issue #360).

# CI test results route extracted to routers/repos.py (issue #360).

# CI test rerun route extracted to routers/repos.py (issue #360).

# Stats route extracted to routers/repos.py (issue #360).

# Usage monitoring route extracted to routers/repos.py (issue #360).

# ─── Job Queue API ───────────────────────────────────────────────────────────


# Queue management routes extracted to routers/queue.py and registered via app.include_router.


# ─── Fleet Node Aggregation API ──────────────────────────────────────────────


# Routes /api/fleet/nodes, /api/fleet/hardware extracted to routers/orchestration.py (issue #359).
# _get_fleet_nodes_impl kept here and injected via set_dependencies().


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


# /api/fleet/nodes/{node_name}/system extracted to routers/orchestration.py (issue #359).


# ─── Request logging middleware ───────────────────────────────────────────────


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)
    path = request.url.path
    status = response.status_code

    # Always log errors regardless of path — incident reconstruction requires them.
    is_error = status >= 400

    # High-volume paths are sampled at 1/10 to reduce noise without losing
    # visibility.  The filter list is configurable via dashboard_config.LOG_FILTER_PATHS
    # (env var LOG_FILTER_PATHS, comma-separated path prefixes).
    is_filtered = path.startswith(dashboard_config.LOG_FILTER_PATHS)

    if is_error or not is_filtered or random.random() < 0.1:
        # request_id is injected into the log record by RequestIdLogFilter;
        # include it explicitly in the message for plain-text log consumers.
        rid = getattr(request.state, "request_id", "-")
        log.info(
            "%s %s → %s (%sms) [rid=%s]",
            request.method,
            path,
            status,
            elapsed,
            rid,
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


# Assessments routes extracted to routers/assessments.py and registered via app.include_router (issue #358).


# Fleet orchestration routes, audit functions, and lock moved to
# orchestration_audit.py and routers/orchestration.py (issue #359).


# ─── Diagnostics & Launchers ──────────────────────────────────────────────────


# GET /api/deployment/git-drift extracted to routers/deployment.py (issue #357).


# Diagnostics summary route extracted to routers/diagnostics.py (issue #360).

# Restart-service route extracted to routers/diagnostics.py (issue #360).

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
# Routes and logic extracted to routers/runner_audit.py (issue #298 batch 3).
# The audit background loop is started in _startup() via
# _runner_audit_router.start_audit_loop() below.

HOSTED_RUNNER_PATTERNS = re.compile(
    r"^(ubuntu-|windows-|macos-|GitHub Actions \d|Hosted Agent)",
    re.IGNORECASE,
)
_runner_audit_cache: dict[str, Any] = {
    "violations": [],
    "last_checked": None,
    "error": None,
}
_runner_audit_lock = asyncio.Lock()


# Inject dependencies into system router
_system_router.set_boot_time(BOOT_TIME)

# Inject dependencies into orchestration router (issue #359)
_orchestration_router.set_dependencies(
    fleet_control_local=_fleet_control_local,
    remote_fleet_control=_remote_fleet_control,
    get_fleet_nodes_impl=_get_fleet_nodes_impl,
    proxy_to_hub=proxy_to_hub,
    should_proxy_fleet_to_hub=_should_proxy_fleet_to_hub,
    get_runner_capacity_snapshot=get_runner_capacity_snapshot,
    validate_runner_schedule=_validate_runner_schedule,
    write_runner_schedule_config=_write_runner_schedule_config,
    runner_scheduler_apply_command=_runner_scheduler_apply_command,
    run_cmd=run_cmd,
    get_system_metrics_snapshot=get_system_metrics_snapshot,
    runner_scheduler_bin=RUNNER_SCHEDULER_BIN,
    runner_schedule_config=RUNNER_SCHEDULE_CONFIG,
    runner_scheduler_state=RUNNER_SCHEDULER_STATE,
    runner_base_dir=RUNNER_BASE_DIR,
)
_system_router.set_host_memory_gb(HOST_MEMORY_GB)
_system_router.set_runner_capacity_snapshot_func(get_runner_capacity_snapshot)

# Inject dependencies into deployment router
_deployment_router.set_dependencies(
    get_fleet_nodes_impl=_get_fleet_nodes_impl,
    proxy_to_hub=proxy_to_hub,
    should_proxy_fleet_to_hub=_should_proxy_fleet_to_hub,
    deployment_info=_deployment_info,
    read_expected_dashboard_version=_read_expected_dashboard_version,
    build_deployment_state=_build_deployment_state,
)

# Inject dependencies into reports/heavy_tests/assessments routers
_reports_router.set_reports_dir(REPORTS_DIR)
_heavy_tests_router.set_dependencies(run_cmd=run_cmd, heavy_test_repos=HEAVY_TEST_REPOS)
_assessments_router.set_run_cmd(run_cmd)


_leader_lock_fd = None

# Runner routing audit refresh route extracted to routers/diagnostics.py (issue #360).

@app.on_event("startup")
async def _startup() -> None:
    """Initialize HTTP clients and notify systemd on startup (issue #364)."""
    # Wire injected dependencies for extracted routers (issue #360)
    _repos_router.set_dependencies(
        cache_get=_cache_get,
        cache_set=_cache_set,
        cache_delete=_cache_delete,
        run_cmd=run_cmd,
        gh_api_admin=gh_api_admin,
        get_recent_org_repos=_get_recent_org_repos,
        get_fleet_nodes_impl=_get_fleet_nodes_impl,
        queue_impl=_queue_impl,
        pr_inventory=pr_inventory,
        issue_inventory=issue_inventory,
        linear_router=_linear_router,
        linear_inventory=linear_inventory,
        unified_issue_inventory=unified_issue_inventory,
        lease_synchronizer=lease_synchronizer,
        usage_monitoring=usage_monitoring,
        org=ORG,
        repos_ttl=float(CacheTtl.REPOS_S),
        ci_test_results_ttl=float(CacheTtl.CI_TEST_RESULTS_S),
        stats_ttl=float(CacheTtl.STATS_S),
        usage_monitoring_ttl=float(CacheTtl.USAGE_MONITORING_S),
    )
    _diagnostics_router.set_dependencies(
        get_git_drift=_deployment_router.get_git_drift,
        port=PORT,
        systemctl_bin=SYSTEMCTL_BIN,
        run_runner_audit_fn=_run_runner_audit,
    )
    # Initialize pooled HTTP clients
    initialize_http_clients()
    log.info("Initialized pooled HTTP clients with connection reuse")

    # Notify systemd that we are ready (issue #391 AC-3)
    if _sd_notify is not None:
        _sd_notify("READY=1\nWATCHDOG_USEC=120000000")  # 120s in microseconds
        log.info("Sent systemd READY=1 notification")
    else:
        log.debug("systemd.daemon not available; omitting sd_notify")

    # Replay-store purge runs on every node regardless of leader status.
    asyncio.create_task(_periodic_replay_purge())

    # Inject org into the audit router so it can query GitHub (issue #298)
    _runner_audit_router.set_org(ORG)

    if os.environ.get("DASHBOARD_LEADER") == "1":
        _runner_audit_router.start_audit_loop()
        _linear_sync_router.start_sync_loop()  # issue #236
        return

    try:
        import fcntl

        global _leader_lock_fd
        lock_path = "/var/run/runner-dashboard-leader.lock"
        if not os.path.exists(os.path.dirname(lock_path)):
            lock_path = "/tmp/runner-dashboard-leader.lock"
        _leader_lock_fd = open(lock_path, "w")
        fcntl.flock(_leader_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]  # type: ignore[attr-defined]
        log.info("Acquired leader lock, starting background tasks")
        _runner_audit_router.start_audit_loop()
        _linear_sync_router.start_sync_loop()  # issue #236
    except ImportError:
        log.warning("fcntl not available on this platform, running without file lock")
        _runner_audit_router.start_audit_loop()
        _linear_sync_router.start_sync_loop()  # issue #236
    except OSError as e:
        log.info("Could not acquire leader lock, running as follower: %s", e)


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Close pooled HTTP clients on shutdown (issue #364)."""
    await shutdown_http_clients()
    log.info("Closed pooled HTTP clients")


# ─── Main ─────────────────────────────────────────────────────────────────────


def _read_uvicorn_env_config() -> dict[str, int]:
    """Read uvicorn tuning knobs from environment variables (#393).

    Returns a dict with ``workers``, ``limit_concurrency`` and
    ``timeout_keep_alive``.
    """

    def _int_env(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            log.warning("Invalid %s=%r, falling back to %d", name, raw, default)
            return default

    return {
        "workers": _int_env("WORKERS", 1),
        "limit_concurrency": _int_env("LIMIT_CONCURRENCY", 200),
        "timeout_keep_alive": _int_env("TIMEOUT_KEEP_ALIVE", 5),
    }


if __name__ == "__main__":
    import uvicorn

    log.info("=" * 60)
    log.info("  D-sorganization Runner Dashboard v4.0")
    log.info("  Local:   http://localhost:%s", PORT)
    log.info("  Network: http://0.0.0.0:%s", PORT)
    log.info("  API docs: http://localhost:%s/docs", PORT)
    log.info("  Health:   http://localhost:%s/api/health", PORT)
    log.info("  Org: %s | Host: %s", ORG, HOSTNAME)
    log.info("  Runners: %s @ %s", NUM_RUNNERS, RUNNER_BASE_DIR)
    log.info("=" * 60)

    _uvicorn_cfg = _read_uvicorn_env_config()

    # Issue #367: keep the documented single-worker default. Operators can set
    # WORKERS > 1, but uvicorn then requires an import string (not the
    # in-memory app object) because workers spawn via multiprocessing and each
    # child re-imports the app. Codex P1 review on PR #482 flagged that passing
    # `app` directly with `workers > 1` either silently runs a single worker or
    # fails at startup. Use the import string when WORKERS > 1; keep the
    # in-memory object for single-worker dev runs (faster, no re-import).
    _uvicorn_target: object = "server:app" if _uvicorn_cfg["workers"] > 1 else app
    uvicorn.run(
        _uvicorn_target,  # type: ignore[arg-type]
        host="0.0.0.0",  # B104: intentionally binding to all interfaces; listed in bandit.yaml
        port=PORT,
        log_level="warning",  # FastAPI handles its own logging
        workers=_uvicorn_cfg["workers"],
        limit_concurrency=_uvicorn_cfg["limit_concurrency"],
        timeout_keep_alive=_uvicorn_cfg["timeout_keep_alive"],
    )
# ci-trigger
