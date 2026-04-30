"""Middleware and helper modules extracted from server.py (issue #299).

This package contains:
- caching: Cache management (get/set with TTL)
- command_execution: Subprocess and GitHub API execution
- envelope_deduplication: Request replay detection
- proxy_utils: Hub/spoke proxying utilities
- request_logging: Request/response logging middleware
- Plus security.py in the backend root (see backend/security.py)
"""

from .caching import cache_clear, cache_get, cache_get_internal, cache_set
from .command_execution import gh_api, gh_api_admin, gh_api_raw, run_cmd
from .envelope_deduplication import is_envelope_replay, record_processed_envelope
from .proxy_utils import HUB_URL, MACHINE_ROLE, _set_hub_config, proxy_to_hub, should_proxy_fleet_to_hub
from .request_logging import log_requests

__all__ = [
    # caching
    "cache_clear",
    "cache_get",
    "cache_get_internal",
    "cache_set",
    # command_execution
    "gh_api",
    "gh_api_admin",
    "gh_api_raw",
    "run_cmd",
    # envelope_deduplication
    "is_envelope_replay",
    "record_processed_envelope",
    # proxy_utils
    "HUB_URL",
    "MACHINE_ROLE",
    "_set_hub_config",
    "proxy_to_hub",
    "should_proxy_fleet_to_hub",
    # request_logging
    "log_requests",
]
