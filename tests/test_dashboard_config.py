from __future__ import annotations  # noqa: E402

import importlib  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

import dashboard_config  # noqa: E402


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_backend_dir() -> None:
    assert dashboard_config.BACKEND_DIR == Path(__file__).parent.parent.resolve() / "backend"


def test_runner_base_dir() -> None:
    assert dashboard_config.RUNNER_BASE_DIR == Path.home() / "actions-runners"


# ---------------------------------------------------------------------------
# Org & Runner limits
# ---------------------------------------------------------------------------


def test_default_num_runners() -> None:
    assert dashboard_config.DEFAULT_NUM_RUNNERS == 12


def test_num_runners_respects_max(monkeypatch) -> None:
    env = os.environ.copy()
    env["NUM_RUNNERS"] = "20"
    env["MAX_RUNNERS"] = "15"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.REQUESTED_NUM_RUNNERS == 20
    assert dashboard_config.MAX_RUNNERS == 15
    assert dashboard_config.NUM_RUNNERS == 15


def test_num_runners_below_max(monkeypatch) -> None:
    env = os.environ.copy()
    env["NUM_RUNNERS"] = "8"
    env["MAX_RUNNERS"] = "15"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.NUM_RUNNERS == 8


def test_num_runners_defaults(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("NUM_RUNNERS", "MAX_RUNNERS")}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.NUM_RUNNERS == dashboard_config.DEFAULT_NUM_RUNNERS


# ---------------------------------------------------------------------------
# Runner aliases
# ---------------------------------------------------------------------------


def test_runner_aliases_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["RUNNER_ALIASES"] = "alpha,Beta,  gamma  "
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.RUNNER_ALIASES == ["alpha", "beta", "gamma"]


def test_runner_aliases_empty(monkeypatch) -> None:
    env = os.environ.copy()
    env["RUNNER_ALIASES"] = ""
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.RUNNER_ALIASES == []


# ---------------------------------------------------------------------------
# Disk thresholds
# ---------------------------------------------------------------------------


def test_disk_thresholds_defaults(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DASHBOARD_DISK")}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.DISK_WARN_PERCENT == 85.0
    assert dashboard_config.DISK_CRITICAL_PERCENT == 92.0
    assert dashboard_config.DISK_MIN_FREE_GB == 25.0


def test_disk_thresholds_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["DASHBOARD_DISK_WARN_PERCENT"] = "90"
    env["DASHBOARD_DISK_CRITICAL_PERCENT"] = "95"
    env["DASHBOARD_DISK_MIN_FREE_GB"] = "10"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.DISK_WARN_PERCENT == 90.0
    assert dashboard_config.DISK_CRITICAL_PERCENT == 95.0
    assert dashboard_config.DISK_MIN_FREE_GB == 10.0


# ---------------------------------------------------------------------------
# Port & Hostname
# ---------------------------------------------------------------------------


def test_port_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "DASHBOARD_PORT"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.PORT == 8321


def test_port_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["DASHBOARD_PORT"] = "9000"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.PORT == 9000


# ---------------------------------------------------------------------------
# runner_limit
# ---------------------------------------------------------------------------


def test_runner_limit(monkeypatch) -> None:
    env = os.environ.copy()
    env["NUM_RUNNERS"] = "5"
    env["MAX_RUNNERS"] = "10"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.runner_limit() == 10


# ---------------------------------------------------------------------------
# HUB_URL
# ---------------------------------------------------------------------------


def test_hub_url_none_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "HUB_URL"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.HUB_URL is None


def test_hub_url_strips_trailing_slash(monkeypatch) -> None:
    env = os.environ.copy()
    env["HUB_URL"] = "https://hub.example.com/"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.HUB_URL == "https://hub.example.com"


# ---------------------------------------------------------------------------
# FLEET_NODES
# ---------------------------------------------------------------------------


def test_fleet_nodes_empty(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "FLEET_NODES"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.FLEET_NODES == {}


def test_fleet_nodes_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["FLEET_NODES"] = "node1:http://n1.local,node2:http://n2.local/"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.FLEET_NODES == {
        "node1": "http://n1.local",
        "node2": "http://n2.local",
    }


# ---------------------------------------------------------------------------
# Cache / UI Limits
# ---------------------------------------------------------------------------


def test_cache_limits_defaults() -> None:
    assert dashboard_config.MAX_CACHE_SIZE == 500
    assert dashboard_config.CACHE_EVICT_BATCH == 50


def test_run_job_enrichment_limit_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "RUN_JOB_ENRICHMENT_LIMIT"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.RUN_JOB_ENRICHMENT_LIMIT == 50


def test_run_job_enrichment_limit_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["RUN_JOB_ENRICHMENT_LIMIT"] = "100"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.RUN_JOB_ENRICHMENT_LIMIT == 100


# ---------------------------------------------------------------------------
# Scheduler / Services
# ---------------------------------------------------------------------------


def test_runner_scheduler_bin_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "RUNNER_SCHEDULER_BIN"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.RUNNER_SCHEDULER_BIN == "/usr/local/bin/runner-scheduler"


def test_systemctl_bin_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "SYSTEMCTL_BIN"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.SYSTEMCTL_BIN == "/usr/bin/systemctl"


def test_runner_scheduler_apply_command_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "RUNNER_SCHEDULER_APPLY_CMD"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    cmd = dashboard_config.runner_scheduler_apply_command()
    assert cmd[0] == dashboard_config.RUNNER_SCHEDULER_BIN
    assert cmd[1] == "apply"


def test_runner_scheduler_apply_command_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["RUNNER_SCHEDULER_APPLY_CMD"] = "custom apply"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.runner_scheduler_apply_command() == ["custom", "apply"]


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


def test_version() -> None:
    assert dashboard_config.VERSION == "1.2.0"


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


def test_default_llm_model_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "DASHBOARD_LLM_MODEL"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.DEFAULT_LLM_MODEL == "claude-haiku-4-5-20251001"


def test_default_llm_model_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["DASHBOARD_LLM_MODEL"] = "gpt-4"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.DEFAULT_LLM_MODEL == "gpt-4"


# ---------------------------------------------------------------------------
# Heavy Test Repos
# ---------------------------------------------------------------------------


def test_heavy_test_repos_structure() -> None:
    assert "Repository_Management" in dashboard_config.HEAVY_TEST_REPOS
    repo = dashboard_config.HEAVY_TEST_REPOS["Repository_Management"]
    assert repo["workflow_file"] == "ci-heavy-integration-tests.yml"
    assert repo["description"] == "Heavy Integration Suite"
    assert "python_versions" in repo
    assert repo["default_python"] == "3.12"


# ---------------------------------------------------------------------------
# Session Secret
# ---------------------------------------------------------------------------


def test_session_secret_from_env(monkeypatch) -> None:
    env = os.environ.copy()
    env["SESSION_SECRET"] = "my-secret-value"
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.SESSION_SECRET == "my-secret-value"


def test_session_secret_generated(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "SESSION_SECRET"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert isinstance(dashboard_config.SESSION_SECRET, str)
    assert len(dashboard_config.SESSION_SECRET) == 64  # 32 bytes hex = 64 chars


# ---------------------------------------------------------------------------
# MACHINE_ROLE
# ---------------------------------------------------------------------------


def test_machine_role_default(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "MACHINE_ROLE"}
    monkeypatch.setattr(os, "environ", env)
    importlib.reload(dashboard_config)
    assert dashboard_config.MACHINE_ROLE == "node"
