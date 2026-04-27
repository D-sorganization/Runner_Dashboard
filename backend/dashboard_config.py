"""Configuration constants for runner-dashboard."""

from __future__ import annotations

import os
import platform
import secrets
from pathlib import Path

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

# Disk Thresholds
DISK_WARN_PERCENT = float(os.environ.get("DASHBOARD_DISK_WARN_PERCENT", "85"))
DISK_CRITICAL_PERCENT = float(os.environ.get("DASHBOARD_DISK_CRITICAL_PERCENT", "92"))
DISK_MIN_FREE_GB = float(os.environ.get("DASHBOARD_DISK_MIN_FREE_GB", "25"))

# API / Port
PORT = int(os.environ.get("DASHBOARD_PORT", "8321"))
HOSTNAME = os.environ.get("DISPLAY_NAME") or platform.node()

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
WSL_KEEPALIVE_SERVICE = os.environ.get("WSL_KEEPALIVE_SERVICE", "wsl-runner-keepalive.service")
WSL_KEEPALIVE_TASK_NAME = os.environ.get("WSL_KEEPALIVE_TASK_NAME", "WSL-Runner-KeepAlive")

# Deployment
DEPLOYMENT_FILE = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_DEPLOYMENT_FILE",
        BACKEND_DIR.parent / "deployment.json",
    )
)
EXPECTED_VERSION_FILE = Path(
    os.environ.get(
        "RUNNER_DASHBOARD_EXPECTED_VERSION_FILE",
        BACKEND_DIR.parent / "VERSION",
    )
)

# LLM
DEFAULT_LLM_MODEL = os.environ.get("DASHBOARD_LLM_MODEL", "claude-haiku-4-5-20251001")

# Heavy Test Repos
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

# Session
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
