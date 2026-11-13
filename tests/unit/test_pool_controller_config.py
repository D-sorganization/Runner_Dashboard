"""Pool controller feature flag defaults off (#4279)."""

from __future__ import annotations

import os
from unittest.mock import patch

from pool_controller.config import pool_controller_enabled


def test_pool_controller_disabled_by_default() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert pool_controller_enabled() is False


def test_pool_controller_enabled_when_env_set() -> None:
    with patch.dict(os.environ, {"DASHBOARD_ENABLE_POOL_CONTROLLER": "1"}):
        assert pool_controller_enabled() is True
