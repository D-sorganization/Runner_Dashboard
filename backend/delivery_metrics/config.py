"""Delivery metrics feature surface (default off until slice 4 wires the API)."""

from __future__ import annotations

import os

from dashboard_config import ORG, _env_flag

_FLAG_ENV = "DASHBOARD_ENABLE_DELIVERY_METRICS"


def delivery_metrics_enabled() -> bool:
    """Return True when the delivery-metrics stack is enabled."""
    return _env_flag(_FLAG_ENV, default=False)


def delivery_metrics_repos() -> list[str]:
    """Repos to sync (``owner/name``). Defaults to this dashboard repo."""
    raw = os.environ.get("DELIVERY_METRICS_REPOS", "").strip()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [f"{ORG}/Runner_Dashboard"]
