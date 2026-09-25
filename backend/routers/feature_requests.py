"""Deprecated module alias: use backend.routers.code_requests instead (CR-1, #1281)."""

import sys

from routers import code_requests

# Point sys.modules to code_requests so backwards-compatible imports and test patches
# targeting routers.feature_requests directly mutate routers.code_requests.
sys.modules[__name__] = code_requests
