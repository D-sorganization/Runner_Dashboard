"""Configured cooldown durations remain safe inputs to deadline construction (#4518)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from staff import provider_failover_config as config

pytestmark = pytest.mark.unit


@pytest.fixture
def cooldown_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Use an isolated config file and discard its cached values after each test."""
    path = tmp_path / "provider_failover.json"
    monkeypatch.setattr(config, "_CONFIG_PATH", path)
    config.reset_failover_config_cache()
    yield path
    config.reset_failover_config_cache()


@pytest.mark.parametrize("failure_class", ["rate_limited", "custom_failure"])
@pytest.mark.parametrize("invalid", ["NaN", float("nan"), "Infinity", float("-inf"), -1, -0.5, 10**400, "bad", None])
def test_invalid_duration_uses_fail_closed_fallback(cooldown_config: Path, failure_class: str, invalid: object) -> None:
    """Malformed or out-of-domain values keep the existing one-hour fallback."""
    cooldown_config.write_text(json.dumps({"cooldown_seconds": {failure_class: invalid}}), encoding="utf-8")
    assert config.failover_cooldown_seconds()[failure_class] == 3600.0


@pytest.mark.parametrize("duration", [0, 2, 0.5, "120.5", 7200])
def test_finite_nonnegative_duration_is_preserved(cooldown_config: Path, duration: float | str) -> None:
    """Valid overrides retain their value, including an explicit zero delay."""
    cooldown_config.write_text(json.dumps({"cooldown_seconds": {"rate_limited": duration}}), encoding="utf-8")
    assert config.failover_cooldown_seconds()["rate_limited"] == float(duration)


def test_invalid_override_does_not_replace_other_defaults(cooldown_config: Path) -> None:
    """Repair only the invalid entry and preserve other configured durations."""
    cooldown_config.write_text(
        json.dumps({"cooldown_seconds": {"rate_limited": "NaN", "provider_capacity": 42}}), encoding="utf-8"
    )
    assert config.failover_cooldown_seconds() == {
        "rate_limited": 3600.0,
        "provider_capacity": 42.0,
        "quota_exhausted": 3600.0,
    }
