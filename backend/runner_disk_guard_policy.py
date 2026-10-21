"""Runner disk guard policy loader (RD-OPS-017).

Loads RM-OPS-017 exclusively through the vendored
``shared_scripts.runner_disk_guard_config`` validator.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from shared_scripts.runner_disk_guard_config import (
    RunnerDiskGuardConfig,
    load_runner_disk_guard_config,
    resolve_runner_disk_guard_config_path,
)


@lru_cache(maxsize=1)
def load_runner_disk_guard_policy(path: str | None = None) -> RunnerDiskGuardConfig:
    """Load validated policy (cached per process)."""
    config_path = Path(path) if path else resolve_runner_disk_guard_config_path()
    return load_runner_disk_guard_config(config_path)


def clear_runner_disk_guard_policy_cache() -> None:
    load_runner_disk_guard_policy.cache_clear()
