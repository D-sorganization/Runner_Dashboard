"""Delivery metrics feature surface (default off until slice 4 wires the API)."""

from __future__ import annotations

import os

_FLAG_ENV = "DASHBOARD_ENABLE_DELIVERY_METRICS"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def delivery_metrics_enabled() -> bool:
    """Return True when the delivery-metrics stack is enabled."""
    return _env_flag(_FLAG_ENV, default=False)
