"""RM-OPS-017 disk-full cordon configuration (Repository_Management #2221).

The canonical schema lives in Repository_Management; this loader accepts that
JSON shape and degrades to documented defaults when the file is absent.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.rm_ops_017_config")

_DEFAULT_REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "rm-ops-017.json"


@dataclass(frozen=True, slots=True)
class DiskFullCordonTripThresholds:
    """Trip when free space is below *either* bound (OR, same as host volume guard)."""

    free_percent_below: float
    free_gb_below: float


@dataclass(frozen=True, slots=True)
class DiskFullCordonReadmitThresholds:
    """Re-admit only when free space is above *both* bounds (hysteresis)."""

    free_percent_above: float
    free_gb_above: float


@dataclass(frozen=True, slots=True)
class DiskFullCordonConfig:
    """RM-OPS-017 / RD-OPS-017 disk-full cordon policy."""

    enabled: bool
    trip: DiskFullCordonTripThresholds
    readmit: DiskFullCordonReadmitThresholds
    drain_timeout_seconds: float
    multi_host_cordon_alert_min: int

    @staticmethod
    def defaults() -> DiskFullCordonConfig:
        return DiskFullCordonConfig(
            enabled=True,
            trip=DiskFullCordonTripThresholds(free_percent_below=5.0, free_gb_below=30.0),
            readmit=DiskFullCordonReadmitThresholds(free_percent_above=10.0, free_gb_above=50.0),
            drain_timeout_seconds=3600.0,
            multi_host_cordon_alert_min=2,
        )


def _coerce_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"RM-OPS-017: {field} must be a number") from exc


def _coerce_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"RM-OPS-017: {field} must be an integer") from exc


def parse_disk_full_cordon_config(payload: Mapping[str, Any] | None) -> DiskFullCordonConfig:
    """Parse the ``disk_full_cordon`` object from an RM-OPS-017 config document."""
    base = DiskFullCordonConfig.defaults()
    if not payload:
        return base
    section = payload.get("disk_full_cordon") if isinstance(payload, Mapping) else None
    if not isinstance(section, dict):
        return base

    enabled = bool(section.get("enabled", base.enabled))
    trip_raw = section.get("trip") if isinstance(section.get("trip"), dict) else {}
    readmit_raw = section.get("readmit") if isinstance(section.get("readmit"), dict) else {}

    trip = DiskFullCordonTripThresholds(
        free_percent_below=_coerce_float(
            trip_raw.get("free_percent_below", base.trip.free_percent_below),
            "trip.free_percent_below",
        ),
        free_gb_below=_coerce_float(trip_raw.get("free_gb_below", base.trip.free_gb_below), "trip.free_gb_below"),
    )
    readmit = DiskFullCordonReadmitThresholds(
        free_percent_above=_coerce_float(
            readmit_raw.get("free_percent_above", base.readmit.free_percent_above),
            "readmit.free_percent_above",
        ),
        free_gb_above=_coerce_float(
            readmit_raw.get("free_gb_above", base.readmit.free_gb_above),
            "readmit.free_gb_above",
        ),
    )
    drain_timeout = _coerce_float(
        section.get("drain_timeout_seconds", base.drain_timeout_seconds),
        "drain_timeout_seconds",
    )
    if drain_timeout <= 0:
        raise ValueError("RM-OPS-017: drain_timeout_seconds must be > 0")

    alert_min = _coerce_int(
        section.get("multi_host_cordon_alert_min", base.multi_host_cordon_alert_min),
        "multi_host_cordon_alert_min",
    )
    if alert_min < 2:
        raise ValueError("RM-OPS-017: multi_host_cordon_alert_min must be >= 2")

    if readmit.free_percent_above <= trip.free_percent_below:
        raise ValueError("RM-OPS-017: readmit free_percent_above must exceed trip free_percent_below")
    if readmit.free_gb_above <= trip.free_gb_below:
        raise ValueError("RM-OPS-017: readmit free_gb_above must exceed trip free_gb_below")

    return DiskFullCordonConfig(
        enabled=enabled,
        trip=trip,
        readmit=readmit,
        drain_timeout_seconds=drain_timeout,
        multi_host_cordon_alert_min=alert_min,
    )


def resolve_rm_ops_017_config_path() -> Path:
    """Return the config file path (env override, then repo default)."""
    env_path = os.environ.get("RM_OPS_017_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return _DEFAULT_REPO_CONFIG


def load_disk_full_cordon_config(path: Path | None = None) -> DiskFullCordonConfig:
    """Load RM-OPS-017 disk-full cordon settings. Missing file → defaults."""
    config_path = path or resolve_rm_ops_017_config_path()
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        log.debug("RM-OPS-017 config not found at %s; using defaults", config_path)
        return DiskFullCordonConfig.defaults()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"RM-OPS-017 config at {config_path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"RM-OPS-017 config at {config_path} must be a JSON object")
    return parse_disk_full_cordon_config(payload)
