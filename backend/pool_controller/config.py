"""Pool controller feature gate (default off until demand validated, #4250 / #4279)."""

from __future__ import annotations

from dashboard_config import _env_flag

_FLAG_ENV = "DASHBOARD_ENABLE_POOL_CONTROLLER"


def pool_controller_enabled() -> bool:
    """Return True when lane-file and controller slices are active."""
    return _env_flag(_FLAG_ENV, default=False)
