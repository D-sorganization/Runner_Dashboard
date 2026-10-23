"""Operator settings for fleet security scanning (``config/security_scan.json``).

These are the settings upstream oss-scanner reserves for its operators (cadence,
budget, priority) and therefore rejects in a repository's ``project.yaml``. In the
fleet they live here, owned by the dashboard operator, never by the scanned repo.

Contract: :func:`load_operator_config` never raises. A missing or malformed file
yields the safe defaults (every repository disabled, no scan hosts) and the reason in
``errors``. Safety floors cannot be configured away: ``min_free_gb`` is at least
:data:`MIN_FREE_GB_FLOOR`, and only a scan host whose ``isolation`` is ``"vm"`` is
eligible for real scans (panel verdict q4: WSL distros and plain containers on a
credential-bearing host do not count).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.vuln_scan.config")

CONFIG_ENV = "SECURITY_SCAN_CONFIG"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "security_scan.json"
ISOLATIONS = ("vm", "container")
# The runner disk guard cordons at 25 GB and readmits at 40 GB; one image build
# must not push a host back under the cordon (panel verdict q4, Argon).
MIN_FREE_GB_FLOOR = 60
_REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_HOST_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_DOCKER_HOST = re.compile(
    r"^(ssh://[A-Za-z0-9_.@-]+(:\d{1,5})?|unix:///[A-Za-z0-9_./-]+)$"
)
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_NETWORK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_CLI_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_MODEL = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")


@dataclass(frozen=True)
class RepoSettings:
    enabled: bool = False
    full_scan_days: int = 30
    priority: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "full_scan_days": self.full_scan_days,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class ScheduleSettings:
    """Idle-capacity scheduling (#2574). Off until the owner sets ``enabled``."""

    enabled: bool = False
    check_minutes: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "check_minutes": self.check_minutes}


@dataclass(frozen=True)
class ScanHost:
    name: str
    isolation: str
    docker_host: str | None = None
    # Docker network for phase A builds; the host must filter it (DOCKER-USER
    # rules, IPv6 off, fixed DNS). Without one the host cannot build images.
    build_network: str | None = None

    @property
    def eligible_for_real_scans(self) -> bool:
        return self.isolation == "vm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "isolation": self.isolation,
            "docker_host": self.docker_host,
            "build_network": self.build_network,
            "eligible_for_real_scans": self.eligible_for_real_scans,
        }


@dataclass(frozen=True)
class ExecutorSettings:
    """How the scanning agent is launched (panel verdict q4, items 2 and 7)."""

    cli_version: str | None = None  # pinned ``claude --version``; unset = no real scans
    model: str = "claude-opus-5-5"
    max_turns: int = 200
    exec_timeout_s: int = 600
    scan_timeout_minutes: int = 180
    build_timeout_minutes: int = 30
    # A second, different Claude model re-runs each new finding in a fresh
    # container (q4 item 4). None turns verification off; findings then stay new.
    verifier_model: str | None = "claude-sonnet-5-5"
    verify_max_turns: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "cli_version": self.cli_version,
            "model": self.model,
            "max_turns": self.max_turns,
            "exec_timeout_s": self.exec_timeout_s,
            "scan_timeout_minutes": self.scan_timeout_minutes,
            "build_timeout_minutes": self.build_timeout_minutes,
            "verifier_model": self.verifier_model,
            "verify_max_turns": self.verify_max_turns,
        }


@dataclass(frozen=True)
class OperatorConfig:
    defaults: RepoSettings = RepoSettings()
    repos: dict[str, RepoSettings] = field(default_factory=dict)
    scan_hosts: tuple[ScanHost, ...] = ()
    window_start: str = "01:00"
    window_end: str = "06:00"
    timezone: str = "America/Los_Angeles"
    min_free_gb: int = MIN_FREE_GB_FLOOR
    max_new_findings_per_scan: int = 10
    usd_per_day: float = 5.0
    alerts_ttl_seconds: int = 6 * 3600
    executor: ExecutorSettings = ExecutorSettings()
    sarif_export: bool = False  # opt-in (q3): SARIF can leak findings when uploaded
    schedule: ScheduleSettings = ScheduleSettings()
    errors: tuple[str, ...] = ()

    def repo(self, name: str) -> RepoSettings:
        return self.repos.get(name, self.defaults)

    def to_dict(self) -> dict[str, Any]:
        return {
            "defaults": self.defaults.to_dict(),
            "repos": {k: v.to_dict() for k, v in sorted(self.repos.items())},
            "scan_hosts": [h.to_dict() for h in self.scan_hosts],
            "window": {
                "start": self.window_start,
                "end": self.window_end,
                "timezone": self.timezone,
            },
            "min_free_gb": self.min_free_gb,
            "max_new_findings_per_scan": self.max_new_findings_per_scan,
            "usd_per_day": self.usd_per_day,
            "alerts_ttl_seconds": self.alerts_ttl_seconds,
            "executor": self.executor.to_dict(),
            "sarif_export": self.sarif_export,
            "schedule": self.schedule.to_dict(),
            "errors": list(self.errors),
        }


def _schedule(raw: Any, errors: list[str]) -> ScheduleSettings:
    if raw is None:
        return ScheduleSettings()
    if not isinstance(raw, dict):
        errors.append("schedule must be an object")
        return ScheduleSettings()
    return ScheduleSettings(
        enabled=_bool(raw.get("enabled"), "schedule.enabled", errors),
        check_minutes=_int(
            raw.get("check_minutes"), "schedule.check_minutes", 5, 1440, 30, errors
        ),
    )


def _bool(raw: Any, key: str, errors: list[str]) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        errors.append(f"{key} must be true or false")
        return False
    return raw


def _int(raw: Any, key: str, lo: int, hi: int, default: int, errors: list[str]) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not lo <= raw <= hi:
        errors.append(f"{key} must be an integer in [{lo}, {hi}]")
        return default
    return raw


def _repo_settings(
    raw: Any, base: RepoSettings, where: str, errors: list[str]
) -> RepoSettings:
    if not isinstance(raw, dict):
        errors.append(f"{where} must be an object")
        return base
    enabled = raw.get("enabled", base.enabled)
    if not isinstance(enabled, bool):
        errors.append(f"{where}.enabled must be true or false")
        enabled = base.enabled
    days = _int(
        raw.get("full_scan_days"),
        f"{where}.full_scan_days",
        1,
        365,
        base.full_scan_days,
        errors,
    )
    priority = _int(
        raw.get("priority"), f"{where}.priority", 0, 9, base.priority, errors
    )
    return RepoSettings(enabled=enabled, full_scan_days=days, priority=priority)


def _scan_hosts(raw: Any, errors: list[str]) -> tuple[ScanHost, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        errors.append("scan_hosts must be an object of name -> host")
        return ()
    hosts: list[ScanHost] = []
    for name, spec in sorted(raw.items()):
        if not _HOST_NAME.match(str(name)) or not isinstance(spec, dict):
            errors.append(f"scan_hosts.{name} is not a valid host entry")
            continue
        isolation = spec.get("isolation")
        docker_host = spec.get("docker_host")
        if isolation not in ISOLATIONS:
            errors.append(f"scan_hosts.{name}.isolation must be one of {ISOLATIONS}")
            continue
        if docker_host is not None and not (
            isinstance(docker_host, str) and _DOCKER_HOST.match(docker_host)
        ):
            errors.append(
                f"scan_hosts.{name}.docker_host must be ssh://host or unix:///path"
            )
            continue
        network = spec.get("build_network")
        if network is not None and not (
            isinstance(network, str) and _NETWORK.match(network)
        ):
            errors.append(f"scan_hosts.{name}.build_network is not a network name")
            continue
        hosts.append(
            ScanHost(
                name=name,
                isolation=isolation,
                docker_host=docker_host,
                build_network=network,
            )
        )
    return tuple(hosts)


def _executor(raw: Any, errors: list[str]) -> ExecutorSettings:
    if raw is None:
        return ExecutorSettings()
    if not isinstance(raw, dict):
        errors.append("executor must be an object")
        return ExecutorSettings()
    base = ExecutorSettings()
    version = raw.get("cli_version")
    if version is not None and not (
        isinstance(version, str) and _CLI_VERSION.match(version)
    ):
        errors.append("executor.cli_version must look like 2.1.263")
        version = None
    model = raw.get("model", base.model)
    if not (isinstance(model, str) and _MODEL.match(model)):
        errors.append("executor.model is not a model id")
        model = base.model
    verifier = raw.get("verifier_model", base.verifier_model)
    if verifier is not None and not (
        isinstance(verifier, str) and _MODEL.match(verifier)
    ):
        errors.append("executor.verifier_model is not a model id")
        verifier = base.verifier_model
    if verifier == model:
        errors.append("executor.verifier_model must differ from executor.model")
        verifier = None
    return ExecutorSettings(
        cli_version=version,
        model=model,
        max_turns=_int(
            raw.get("max_turns"), "executor.max_turns", 1, 1000, 200, errors
        ),
        exec_timeout_s=_int(
            raw.get("exec_timeout_s"), "executor.exec_timeout_s", 5, 3600, 600, errors
        ),
        scan_timeout_minutes=_int(
            raw.get("scan_timeout_minutes"),
            "executor.scan_timeout_minutes",
            5,
            720,
            180,
            errors,
        ),
        build_timeout_minutes=_int(
            raw.get("build_timeout_minutes"),
            "executor.build_timeout_minutes",
            1,
            30,
            30,
            errors,
        ),
        verifier_model=verifier,
        verify_max_turns=_int(
            raw.get("verify_max_turns"),
            "executor.verify_max_turns",
            1,
            500,
            100,
            errors,
        ),
    )


def parse_operator_config(payload: Any) -> OperatorConfig:
    """Build an :class:`OperatorConfig` from parsed JSON; problems go to ``errors``."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return OperatorConfig(errors=("config root must be an object",))
    defaults = _repo_settings(
        payload.get("defaults", {}), RepoSettings(), "defaults", errors
    )
    repos: dict[str, RepoSettings] = {}
    raw_repos = payload.get("repos", {})
    if isinstance(raw_repos, dict):
        for name, spec in raw_repos.items():
            if not _REPO_NAME.match(str(name)):
                errors.append(f"repos.{name} is not a valid repository name")
                continue
            repos[name] = _repo_settings(spec, defaults, f"repos.{name}", errors)
    else:
        errors.append("repos must be an object")
    window = (
        payload.get("window", {}) if isinstance(payload.get("window", {}), dict) else {}
    )
    start, end = window.get("start", "01:00"), window.get("end", "06:00")
    if not (
        isinstance(start, str)
        and _HHMM.match(start)
        and isinstance(end, str)
        and _HHMM.match(end)
    ):
        errors.append("window.start and window.end must be HH:MM")
        start, end = "01:00", "06:00"
    timezone = window.get("timezone", "America/Los_Angeles")
    if not isinstance(timezone, str):
        errors.append("window.timezone must be a string")
        timezone = "America/Los_Angeles"
    min_free = _int(
        payload.get("min_free_gb"), "min_free_gb", 0, 10_000, MIN_FREE_GB_FLOOR, errors
    )
    if min_free < MIN_FREE_GB_FLOOR:
        errors.append(f"min_free_gb raised to the {MIN_FREE_GB_FLOOR} GB safety floor")
        min_free = MIN_FREE_GB_FLOOR
    usd = payload.get("usd_per_day", 5.0)
    if isinstance(usd, bool) or not isinstance(usd, int | float) or not 0 <= usd <= 500:
        errors.append("usd_per_day must be a number in [0, 500]")
        usd = 5.0
    return OperatorConfig(
        defaults=defaults,
        repos=repos,
        scan_hosts=_scan_hosts(payload.get("scan_hosts"), errors),
        window_start=start,
        window_end=end,
        timezone=timezone,
        min_free_gb=min_free,
        max_new_findings_per_scan=_int(
            payload.get("max_new_findings_per_scan"),
            "max_new_findings_per_scan",
            1,
            100,
            10,
            errors,
        ),
        usd_per_day=float(usd),
        alerts_ttl_seconds=_int(
            payload.get("alerts_ttl_seconds"),
            "alerts_ttl_seconds",
            600,
            7 * 86400,
            6 * 3600,
            errors,
        ),
        executor=_executor(payload.get("executor"), errors),
        sarif_export=_bool(payload.get("sarif_export"), "sarif_export", errors),
        schedule=_schedule(payload.get("schedule"), errors),
        errors=tuple(errors),
    )


def config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV, "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_PATH


def load_operator_config(path: Path | None = None) -> OperatorConfig:
    """Read the operator config; never raises (safe defaults plus ``errors``)."""
    target = path or config_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return OperatorConfig(errors=(f"{target.name} not found; scanning disabled",))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("security scan config unreadable: %s", exc)
        return OperatorConfig(errors=(f"{target.name} unreadable: {exc}",))
    return parse_operator_config(payload)
