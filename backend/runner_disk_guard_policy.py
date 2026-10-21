"""Runner disk guard policy loader (RD-OPS-017).

Loads RM-OPS-017 exclusively through the vendored
``shared_scripts.runner_disk_guard_config`` validator.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from shared_scripts.runner_disk_guard_config import (
    DEFAULT_CONFIG_PATH,
    RunnerDiskGuardConfig,
    load_runner_disk_guard_config,
)


def _resolve_config_path(path: str | None) -> Path:
    if path:
        return Path(path)
    env_path = os.environ.get("RUNNER_DISK_GUARD_CONFIG_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


@lru_cache(maxsize=1)
def load_runner_disk_guard_policy(path: str | None = None) -> RunnerDiskGuardConfig:
    """Load validated policy (cached per process)."""
    return load_runner_disk_guard_config(_resolve_config_path(path))


def clear_runner_disk_guard_policy_cache() -> None:
    load_runner_disk_guard_policy.cache_clear()
