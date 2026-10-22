"""Delivery metrics feature surface (default off until slice 4 wires the API)."""

from __future__ import annotations

from dashboard_config import _env_flag

_FLAG_ENV = "DASHBOARD_ENABLE_DELIVERY_METRICS"


def delivery_metrics_enabled() -> bool:
    """Return True when the delivery-metrics stack is enabled."""
    return _env_flag(_FLAG_ENV, default=False)
