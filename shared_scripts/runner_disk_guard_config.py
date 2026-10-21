#!/usr/bin/env python3
# Vendored from Repository_Management shared_scripts/runner_disk_guard_config.py
# (RM-OPS-017, PR #2230). Re-sync from upstream; do not fork.
"""Validate and load ``config/runner_disk_guard.yaml`` (schema ``runner-disk-guard/v1``)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_ID = "runner-disk-guard/v1"
DEFAULT_CONFIG_REL = Path("config") / "runner_disk_guard.yaml"


@dataclass(frozen=True, slots=True)
class RunnerDiskGuardTrip:
    min_free_gb: float
    max_used_percent: float


@dataclass(frozen=True, slots=True)
class RunnerDiskGuardDrain:
    finish_running_jobs: bool
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class RunnerDiskGuardReadmit:
    min_free_gb: float
    max_used_percent: float


@dataclass(frozen=True, slots=True)
class RunnerDiskGuardFleet:
    multi_host_cordon_alert_min: int


@dataclass(frozen=True, slots=True)
class RunnerDiskGuardConfig:
    """Validated RM-OPS-017 runner disk guard document."""

    schema: str
    enabled: bool
    trip: RunnerDiskGuardTrip
    drain: RunnerDiskGuardDrain
    readmit: RunnerDiskGuardReadmit
    fleet: RunnerDiskGuardFleet


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"runner disk guard: {field} must be a mapping")
    return value


def _float_field(section: Mapping[str, Any], key: str, field: str) -> float:
    if key not in section:
        raise ValueError(f"runner disk guard: missing {field}")
    try:
        return float(section[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"runner disk guard: {field} must be a number") from exc


def _bool_field(section: Mapping[str, Any], key: str, field: str) -> bool:
    if key not in section:
        raise ValueError(f"runner disk guard: missing {field}")
    value = section[key]
    if isinstance(value, bool):
        return value
    raise ValueError(f"runner disk guard: {field} must be a boolean")


def _int_field(section: Mapping[str, Any], key: str, field: str) -> int:
    if key not in section:
        raise ValueError(f"runner disk guard: missing {field}")
    try:
        return int(section[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"runner disk guard: {field} must be an integer") from exc


def parse_runner_disk_guard_document(document: Mapping[str, Any]) -> RunnerDiskGuardConfig:
    """Parse and validate a loaded YAML document."""
    schema = str(document.get("schema", "")).strip()
    if schema != SCHEMA_ID:
        raise ValueError(f"runner disk guard: schema must be {SCHEMA_ID!r}, got {schema!r}")

    enabled = bool(document.get("enabled", True))
    trip_raw = _require_mapping(document.get("trip"), "trip")
    drain_raw = _require_mapping(document.get("drain"), "drain")
    readmit_raw = _require_mapping(document.get("readmit"), "readmit")
    fleet_raw = _require_mapping(document.get("fleet"), "fleet")

    trip = RunnerDiskGuardTrip(
        min_free_gb=_float_field(trip_raw, "min_free_gb", "trip.min_free_gb"),
        max_used_percent=_float_field(trip_raw, "max_used_percent", "trip.max_used_percent"),
    )
    drain = RunnerDiskGuardDrain(
        finish_running_jobs=_bool_field(drain_raw, "finish_running_jobs", "drain.finish_running_jobs"),
        timeout_seconds=_float_field(drain_raw, "timeout_seconds", "drain.timeout_seconds"),
    )
    readmit = RunnerDiskGuardReadmit(
        min_free_gb=_float_field(readmit_raw, "min_free_gb", "readmit.min_free_gb"),
        max_used_percent=_float_field(readmit_raw, "max_used_percent", "readmit.max_used_percent"),
    )
    fleet = RunnerDiskGuardFleet(
        multi_host_cordon_alert_min=_int_field(
            fleet_raw,
            "multi_host_cordon_alert_min",
            "fleet.multi_host_cordon_alert_min",
        ),
    )

    if trip.min_free_gb <= 0 or readmit.min_free_gb <= 0:
        raise ValueError("runner disk guard: min_free_gb values must be positive")
    if not 0 < trip.max_used_percent <= 100 or not 0 < readmit.max_used_percent <= 100:
        raise ValueError("runner disk guard: max_used_percent must be in (0, 100]")
    if drain.timeout_seconds <= 0:
        raise ValueError("runner disk guard: drain.timeout_seconds must be > 0")
    if fleet.multi_host_cordon_alert_min < 2:
        raise ValueError("runner disk guard: fleet.multi_host_cordon_alert_min must be >= 2")
    if readmit.min_free_gb <= trip.min_free_gb:
        raise ValueError("runner disk guard: readmit.min_free_gb must exceed trip.min_free_gb")
    if readmit.max_used_percent >= trip.max_used_percent:
        raise ValueError("runner disk guard: readmit.max_used_percent must be below trip.max_used_percent")

    return RunnerDiskGuardConfig(
        schema=schema,
        enabled=enabled,
        trip=trip,
        drain=drain,
        readmit=readmit,
        fleet=fleet,
    )


def resolve_runner_disk_guard_config_path(repo_root: Path | None = None) -> Path:
    env_path = os.environ.get("RUNNER_DISK_GUARD_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path)
    root = repo_root or Path(__file__).resolve().parents[1]
    return root / DEFAULT_CONFIG_REL


def load_runner_disk_guard_config(path: Path | None = None, repo_root: Path | None = None) -> RunnerDiskGuardConfig:
    """Load and validate the runner disk guard YAML."""
    config_path = path or resolve_runner_disk_guard_config_path(repo_root)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"runner disk guard config not found: {config_path}") from exc
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"runner disk guard config at {config_path} must be a YAML mapping")
    return parse_runner_disk_guard_document(loaded)
