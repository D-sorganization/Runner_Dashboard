"""Smoke tests for autoscaler_config — env helpers and threshold constants.

The heavy behavioural tests for the autoscaler live in test_runner_autoscaler.py
which imports via ``runner_autoscaler as ra`` (the public facade). These tests
verify the config module's own contract: correct defaults, env-var overrides,
and invalid-input handling.
"""

from __future__ import annotations

import autoscaler_config as cfg
import pytest


class TestEnvFloat:
    def test_default_returned_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("_TEST_FLOAT_VAR", raising=False)
        assert cfg._env_float("_TEST_FLOAT_VAR", 3.14) == pytest.approx(3.14)

    def test_env_value_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_FLOAT_VAR", "1.5")
        assert cfg._env_float("_TEST_FLOAT_VAR", 0.0) == pytest.approx(1.5)

    def test_invalid_env_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_FLOAT_VAR", "not-a-number")
        assert cfg._env_float("_TEST_FLOAT_VAR", 9.9) == pytest.approx(9.9)


class TestEnvInt:
    def test_default_returned_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("_TEST_INT_VAR", raising=False)
        assert cfg._env_int("_TEST_INT_VAR", 42) == 42

    def test_env_value_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_INT_VAR", "7")
        assert cfg._env_int("_TEST_INT_VAR", 0) == 7

    def test_invalid_env_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_INT_VAR", "bad")
        assert cfg._env_int("_TEST_INT_VAR", 5) == 5


def test_constants_have_expected_types() -> None:
    """Sanity: the public constants are the right Python types."""
    assert isinstance(cfg.CPU_HIGH, float)
    assert isinstance(cfg.CPU_LOW, float)
    assert isinstance(cfg.MEM_HIGH, float)
    assert isinstance(cfg.DISK_HIGH, float)
    assert isinstance(cfg.DISK_MIN_FREE_GB, float)
    assert isinstance(cfg.LOAD_PER_CORE, float)
    assert isinstance(cfg.SUSTAIN_SECS, int)
    assert isinstance(cfg.POLL_SECONDS, int)
    assert isinstance(cfg.MIN_ONLINE, int)
    assert isinstance(cfg.MAX_STEP, int)
    assert isinstance(cfg.DRY_RUN, bool)
    assert isinstance(cfg.HOSTNAME, str)


def test_load_per_core_default_is_desktop_safe() -> None:
    """Default AUTOSCALER_LOAD_PER_CORE should protect desktop responsiveness."""
    result = cfg._env_float("AUTOSCALER_LOAD_PER_CORE_UNSET_TEST", 1.2)
    assert result == pytest.approx(1.2)
