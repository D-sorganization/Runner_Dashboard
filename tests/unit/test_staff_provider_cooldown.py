"""Provider cooldown store and summary exposure (#2047 slice 2)."""

from __future__ import annotations

import json
import time

import pytest
from staff.provider_cooldown import (
    cooldown_snapshot,
    mark_provider_cooldown,
    provider_in_cooldown,
    reset_provider_cooldowns,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_provider_cooldowns()


def test_cooldown_expires() -> None:
    mark_provider_cooldown("codex", "provider_capacity", message="retry after 1 seconds")
    assert provider_in_cooldown("codex")
    time.sleep(1.1)
    assert not provider_in_cooldown("codex")


def test_cooldown_snapshot_serializable() -> None:
    mark_provider_cooldown("claude", "quota_exhausted")
    snap = cooldown_snapshot()
    assert snap and snap[0]["provider"] == "claude"
    json.dumps(snap)
