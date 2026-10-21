"""Runner disk-guard policy schema (RM-OPS-017 / #2221).

Consumer: Runner_Dashboard autoscaler (RD-OPS-017 / #2030). On trip the
autoscaler stops accepting jobs, drains running work up to ``drain.timeout_seconds``,
sets ``cordon.min_online_after_drain``, runs ``cleanup.commands``, then re-admits
only when free space and usage satisfy the readmit thresholds (hysteresis).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "runner_disk_guard.yaml"
SCHEMA_ID = "runner-disk-guard/v1"
SUPPORTED_VERSION = 1


class RunnerDiskGuardConfigError(ValueError):
    """Raised when runner disk-guard config fails validation."""


@dataclass(frozen=True)
class DiskGuardThresholds:
    trip_min_free_gb: float
    readmit_min_free_gb: float
    trip_used_percent: float
    readmit_used_percent: float


@dataclass(frozen=True)
class DiskGuardDrain:
    timeout_seconds: int
    finish_running_jobs: bool


@dataclass(frozen=True)
class DiskGuardCordon:
    min_online_after_drain: int
    stop_accepting_jobs_on_trip: bool


@dataclass(frozen=True)
class DiskGuardCleanupCommand:
    argv: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class DiskGuardAlerts:
    cordoned_hosts_warning_threshold: int


@dataclass(frozen=True)
class RunnerDiskGuardConfig:
    version: int
    schema: str
    updated_at: str
    thresholds: DiskGuardThresholds
    drain: DiskGuardDrain
    cordon: DiskGuardCordon
    cleanup: tuple[DiskGuardCleanupCommand, ...]
    alerts: DiskGuardAlerts


def _require_mapping(obj: Any, where: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise RunnerDiskGuardConfigError(f"{where}: must be a mapping")
    return obj


def _require(obj: dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise RunnerDiskGuardConfigError(f"{where}: missing required field '{key}'")
    return obj[key]


def _positive_float(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RunnerDiskGuardConfigError(f"{field}: must be a number")
    number = float(value)
    if number <= 0:
        raise RunnerDiskGuardConfigError(f"{field}: must be > 0, got {number}")
    return number


def _percent(value: Any, field: str) -> float:
    number = _positive_float(value, field)
    if number > 100:
        raise RunnerDiskGuardConfigError(f"{field}: must be <= 100, got {number}")
    return number


def _positive_int(value: Any, field: str, *, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RunnerDiskGuardConfigError(f"{field}: must be an int")
    if value <= 0:
        raise RunnerDiskGuardConfigError(f"{field}: must be > 0, got {value}")
    if maximum is not None and value > maximum:
        raise RunnerDiskGuardConfigError(f"{field}: must be <= {maximum}, got {value}")
    return value


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise RunnerDiskGuardConfigError(f"{field}: must be a boolean")
    return value


def validate_runner_disk_guard(data: dict[str, Any]) -> None:
    """Validate raw YAML mapping.

    Raises :class:`RunnerDiskGuardConfigError` on failure.
    """
    root = _require_mapping(data, "root")
    version = _require(root, "version", "root")
    if version != SUPPORTED_VERSION:
        raise RunnerDiskGuardConfigError(
            f"Unsupported version: {version!r} (expected {SUPPORTED_VERSION})"
        )
    schema = _require(root, "schema", "root")
    if schema != SCHEMA_ID:
        raise RunnerDiskGuardConfigError(
            f"Unexpected schema: {schema!r} (expected {SCHEMA_ID!r})"
        )
    updated_at = _require(root, "updated_at", "root")
    if isinstance(updated_at, str):
        if not updated_at:
            raise RunnerDiskGuardConfigError(
                "root.updated_at must be a non-empty string"
            )
    else:
        # YAML 1.1 may parse ISO dates as datetime.date objects.
        updated_at = str(updated_at)
        if not updated_at:
            raise RunnerDiskGuardConfigError(
                "root.updated_at must be a non-empty string"
            )

    thresholds_raw = _require_mapping(
        _require(root, "thresholds", "root"), "thresholds"
    )
    trip_free = _positive_float(
        _require(thresholds_raw, "trip_min_free_gb", "thresholds"), "trip_min_free_gb"
    )
    readmit_free = _positive_float(
        _require(thresholds_raw, "readmit_min_free_gb", "thresholds"),
        "readmit_min_free_gb",
    )
    if readmit_free <= trip_free:
        raise RunnerDiskGuardConfigError(
            "thresholds.readmit_min_free_gb must be greater than "
            "thresholds.trip_min_free_gb (hysteresis)"
        )
    trip_used = _percent(
        _require(thresholds_raw, "trip_used_percent", "thresholds"), "trip_used_percent"
    )
    readmit_used = _percent(
        _require(thresholds_raw, "readmit_used_percent", "thresholds"),
        "readmit_used_percent",
    )
    if readmit_used >= trip_used:
        raise RunnerDiskGuardConfigError(
            "thresholds.readmit_used_percent must be less than "
            "thresholds.trip_used_percent (hysteresis)"
        )

    drain_raw = _require_mapping(_require(root, "drain", "root"), "drain")
    _positive_int(
        _require(drain_raw, "timeout_seconds", "drain"),
        "drain.timeout_seconds",
        maximum=86400,
    )
    finish = _bool(
        _require(drain_raw, "finish_running_jobs", "drain"),
        "drain.finish_running_jobs",
    )
    if not finish:
        raise RunnerDiskGuardConfigError(
            "drain.finish_running_jobs must be true so running jobs finish "
            "before the host is taken offline (RD-OPS-017)"
        )

    cordon_raw = _require_mapping(_require(root, "cordon", "root"), "cordon")
    min_online = _require(cordon_raw, "min_online_after_drain", "cordon")
    if min_online != 0:
        raise RunnerDiskGuardConfigError(
            "cordon.min_online_after_drain must be 0 during disk-full cordon"
        )
    stop_accept = _bool(
        _require(cordon_raw, "stop_accepting_jobs_on_trip", "cordon"),
        "cordon.stop_accepting_jobs_on_trip",
    )
    if not stop_accept:
        raise RunnerDiskGuardConfigError(
            "cordon.stop_accepting_jobs_on_trip must be true on trip"
        )

    cleanup_raw = _require_mapping(_require(root, "cleanup", "root"), "cleanup")
    commands = _require(cleanup_raw, "commands", "cleanup")
    if not isinstance(commands, list) or not commands:
        raise RunnerDiskGuardConfigError("cleanup.commands must be a non-empty list")
    for index, command in enumerate(commands):
        where = f"cleanup.commands[{index}]"
        cmd = _require_mapping(command, where)
        argv = _require(cmd, "argv", where)
        if not isinstance(argv, list) or not argv:
            raise RunnerDiskGuardConfigError(f"{where}.argv must be a non-empty list")
        for part in argv:
            if not isinstance(part, str) or not part.strip():
                raise RunnerDiskGuardConfigError(
                    f"{where}.argv entries must be non-empty strings"
                )
        description = _require(cmd, "description", where)
        if not isinstance(description, str) or not description.strip():
            raise RunnerDiskGuardConfigError(
                f"{where}.description must be a non-empty string"
            )

    alerts_raw = _require_mapping(_require(root, "alerts", "root"), "alerts")
    cordoned_threshold = _require(
        alerts_raw, "cordoned_hosts_warning_threshold", "alerts"
    )
    if not isinstance(cordoned_threshold, int) or isinstance(cordoned_threshold, bool):
        raise RunnerDiskGuardConfigError(
            "alerts.cordoned_hosts_warning_threshold must be an int"
        )
    if cordoned_threshold < 2:
        raise RunnerDiskGuardConfigError(
            "alerts.cordoned_hosts_warning_threshold must be >= 2"
        )


def parse_runner_disk_guard(data: dict[str, Any]) -> RunnerDiskGuardConfig:
    validate_runner_disk_guard(data)
    thresholds_raw = data["thresholds"]
    drain_raw = data["drain"]
    cordon_raw = data["cordon"]
    cleanup_raw = data["cleanup"]
    alerts_raw = data["alerts"]
    commands = tuple(
        DiskGuardCleanupCommand(
            argv=tuple(entry["argv"]),
            description=entry["description"],
        )
        for entry in cleanup_raw["commands"]
    )
    return RunnerDiskGuardConfig(
        version=data["version"],
        schema=data["schema"],
        updated_at=str(data["updated_at"]),
        thresholds=DiskGuardThresholds(
            trip_min_free_gb=float(thresholds_raw["trip_min_free_gb"]),
            readmit_min_free_gb=float(thresholds_raw["readmit_min_free_gb"]),
            trip_used_percent=float(thresholds_raw["trip_used_percent"]),
            readmit_used_percent=float(thresholds_raw["readmit_used_percent"]),
        ),
        drain=DiskGuardDrain(
            timeout_seconds=int(drain_raw["timeout_seconds"]),
            finish_running_jobs=bool(drain_raw["finish_running_jobs"]),
        ),
        cordon=DiskGuardCordon(
            min_online_after_drain=int(cordon_raw["min_online_after_drain"]),
            stop_accepting_jobs_on_trip=bool(cordon_raw["stop_accepting_jobs_on_trip"]),
        ),
        cleanup=commands,
        alerts=DiskGuardAlerts(
            cordoned_hosts_warning_threshold=int(
                alerts_raw["cordoned_hosts_warning_threshold"]
            ),
        ),
    )


def load_runner_disk_guard_config(path: Path | None = None) -> RunnerDiskGuardConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise RunnerDiskGuardConfigError(f"Config not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise RunnerDiskGuardConfigError(f"{config_path}: root must be a mapping")
    return parse_runner_disk_guard(data)
