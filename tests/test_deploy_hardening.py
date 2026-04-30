"""Static regression checks for deploy hardening.

Includes regression tests for the directives added in issue #391:
MemoryMax, CPUQuota, TasksMax, RestrictAddressFamilies, RestrictNamespaces,
CapabilityBoundingSet, SystemCallFilter, LockPersonality,
MemoryDenyWriteExecute, ProtectHostname, ProtectClock, ProtectProc,
WatchdogSec, and the tightly-scoped sudoers drop-in.
"""

from __future__ import annotations  # noqa: E402

from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEPLOY = _ROOT / "deploy"

# ── Issue #391: new hardening directives ─────────────────────────────────────
# These must appear in both the .service template files AND in the setup.sh
# heredoc so that installed units stay in sync.
_NEW_HARDENING_DIRECTIVES_391 = (
    "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
    "RestrictNamespaces=true",
    "CapabilityBoundingSet=",
    "SystemCallFilter=@system-service",
    "LockPersonality=true",
    "MemoryDenyWriteExecute=true",
    "ProtectHostname=true",
    "ProtectClock=true",
    "ProtectProc=invisible",
    "MemoryMax=2G",
    "CPUQuota=200%",
    "TasksMax=512",
    "WatchdogSec=120",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_shared_deploy_lib_enables_strict_mode() -> None:
    content = _read(_DEPLOY / "lib.sh")
    assert "set -euo pipefail" in content


def test_update_deployed_requires_successful_backup() -> None:
    content = _read(_DEPLOY / "update-deployed.sh")
    assert 'backup_dir "$DEPLOY_DIR") || fail "Backup failed; aborting update"' in content
    assert 'fail "Backup returned empty path; aborting update"' in content


def test_refresh_token_requires_more_than_prefix() -> None:
    content = _read(_DEPLOY / "refresh-token.sh")
    assert "[A-Za-z0-9_]{30,}" in content


def test_setup_prefers_python_311_for_runtime_service() -> None:
    content = _read(_DEPLOY / "setup.sh")
    assert "command -v python3.11 || command -v python3" in content
    assert "ExecStart=${PYTHON_BIN} ${DEPLOY_DIR}/backend/server.py" in content


# ── Issue #391: runner-dashboard.service template ────────────────────────────


def test_dashboard_service_has_restrict_address_families() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX" in content


def test_dashboard_service_has_restrict_namespaces() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "RestrictNamespaces=true" in content


def test_dashboard_service_has_capability_bounding_set() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "CapabilityBoundingSet=" in content


def test_dashboard_service_has_syscall_filter() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "SystemCallFilter=@system-service" in content


def test_dashboard_service_has_lock_personality() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "LockPersonality=true" in content


def test_dashboard_service_has_memory_deny_write_execute() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "MemoryDenyWriteExecute=true" in content


def test_dashboard_service_has_protect_hostname() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "ProtectHostname=true" in content


def test_dashboard_service_has_protect_clock() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "ProtectClock=true" in content


def test_dashboard_service_has_protect_proc() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "ProtectProc=invisible" in content


def test_dashboard_service_has_memory_max() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "MemoryMax=2G" in content


def test_dashboard_service_has_cpu_quota() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "CPUQuota=200%" in content


def test_dashboard_service_has_tasks_max() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "TasksMax=512" in content


def test_dashboard_service_has_watchdog_sec() -> None:
    content = _read(_DEPLOY / "runner-dashboard.service")
    assert "WatchdogSec=120" in content


# ── Issue #391: runner-autoscaler.service template ───────────────────────────


def test_autoscaler_service_has_restrict_address_families() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX" in content


def test_autoscaler_service_has_restrict_namespaces() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "RestrictNamespaces=true" in content


def test_autoscaler_service_has_capability_bounding_set() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "CapabilityBoundingSet=" in content


def test_autoscaler_service_has_syscall_filter() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "SystemCallFilter=@system-service" in content


def test_autoscaler_service_has_lock_personality() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "LockPersonality=true" in content


def test_autoscaler_service_has_memory_deny_write_execute() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "MemoryDenyWriteExecute=true" in content


def test_autoscaler_service_has_protect_hostname() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "ProtectHostname=true" in content


def test_autoscaler_service_has_protect_clock() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "ProtectClock=true" in content


def test_autoscaler_service_has_protect_proc() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "ProtectProc=invisible" in content


def test_autoscaler_service_has_memory_max() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "MemoryMax=2G" in content


def test_autoscaler_service_has_cpu_quota() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "CPUQuota=200%" in content


def test_autoscaler_service_has_tasks_max() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "TasksMax=512" in content


def test_autoscaler_service_has_watchdog_sec() -> None:
    content = _read(_DEPLOY / "runner-autoscaler.service")
    assert "WatchdogSec=120" in content


# ── Issue #391: setup.sh heredoc parity ──────────────────────────────────────


def test_setup_sh_has_new_hardening_directives() -> None:
    """All new #391 directives must be present in setup.sh's heredoc so that
    the installed unit file matches the template."""
    content = _read(_DEPLOY / "setup.sh")
    missing = [d for d in _NEW_HARDENING_DIRECTIVES_391 if d not in content]
    assert not missing, f"setup.sh missing hardening directives from issue #391: {missing}"


# ── Issue #391: sudoers drop-in ───────────────────────────────────────────────


def test_sudoers_dropin_exists() -> None:
    assert (_DEPLOY / "sudoers.d-runner-dashboard").exists(), (
        "deploy/sudoers.d-runner-dashboard must exist (issue #391 AC-6)"
    )


def test_sudoers_dropin_covers_required_unit_names() -> None:
    content = _read(_DEPLOY / "sudoers.d-runner-dashboard")
    assert "actions.runner." in content, "sudoers must cover actions.runner.* units"
    assert "maxwell-daemon.service" in content, "sudoers must cover maxwell-daemon.service"
    assert "runner-scheduler.service" in content, "sudoers must cover runner-scheduler.service"


def test_sudoers_dropin_restricts_to_safe_verbs() -> None:
    content = _read(_DEPLOY / "sudoers.d-runner-dashboard")
    # Must include the read-only and lifecycle verbs.
    assert "is-active" in content
    assert "start" in content
    assert "stop" in content
    assert "restart" in content
    # Must NOT grant broad sudo (e.g., ALL) beyond the Cmnd_Alias.
    lines = [ln.strip() for ln in content.splitlines() if not ln.strip().startswith("#")]
    nopasswd_lines = [ln for ln in lines if "NOPASSWD" in ln]
    for ln in nopasswd_lines:
        assert "ALL=(root) NOPASSWD: RUNNER_DASHBOARD_SYSTEMCTL" in ln or "YOUR_USER" in ln, (
            f"Unexpected broad grant in sudoers: {ln}"
        )


def test_setup_sh_installs_sudoers_dropin() -> None:
    content = _read(_DEPLOY / "setup.sh")
    assert "sudoers.d-runner-dashboard" in content, (
        "setup.sh must reference and install the sudoers drop-in (issue #391 AC-6)"
    )
    assert "/etc/sudoers.d/runner-dashboard" in content
