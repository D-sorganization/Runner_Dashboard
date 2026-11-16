"""Pool controller: lane files, claim-as-queue, and drain semantics (#4279, WP27).

Slice 1 is module-only; orchestration stays behind ``DASHBOARD_ENABLE_POOL_CONTROLLER``.
"""

from pool_controller.config import pool_controller_enabled
from pool_controller.lane_state import LaneState, validate_lane_transition

__all__ = ["LaneState", "pool_controller_enabled", "validate_lane_transition"]
