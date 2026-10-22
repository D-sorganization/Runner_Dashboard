"""Contract tests for autoscaler_config environment parsing and constants."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import autoscaler_config as cfg
import pytest

NUMERIC_ENV_CASES = [
    ("AUTOSCALER_CPU_HIGH", "CPU_HIGH", "91.5", 91.5),
    ("AUTOSCALER_CPU_LOW", "CPU_LOW", "33.5", 33.5),
    ("AUTOSCALER_MEM_HIGH", "MEM_HIGH", "82.5", 82.5),
    ("AUTOSCALER_DISK_HIGH", "DISK_HIGH", "93.5", 93.5),
    ("AUTOSCALER_DISK_MIN_FREE_GB", "DISK_MIN_FREE_GB", "30.5", 30.5),
    ("AUTOSCALER_LOAD_PER_CORE", "LOAD_PER_CORE", "1.8", 1.8),
    ("AUTOSCALER_SUSTAIN_SECS", "SUSTAIN_SECS", "99", 99),
    ("AUTOSCALER_POLL_SECONDS", "POLL_SECONDS", "12", 12),
    ("AUTOSCALER_MIN_ONLINE", "MIN_ONLINE", "2", 2),
    ("AUTOSCALER_RECOVERY_MIN_ONLINE", "RECOVERY_MIN_ONLINE", "4", 4),
    ("AUTOSCALER_MAX_SCALE_STEP", "MAX_STEP", "4", 4),
    ("AUTOSCALER_DRY_RUN", "DRY_RUN", "1", True),
    ("RUNNER_BUSY_LOCK_MAX_AGE_SECONDS", "RUNNER_BUSY_LOCK_MAX_AGE_SECONDS", "600", 600),
    ("RUNNER_PICKUP_DIR_MAX_AGE_SECONDS", "RUNNER_PICKUP_DIR_MAX_AGE_SECONDS", "45", 45),
]


@pytest.fixture(autouse=True)
def _restore_config_module(monkeypatch: pytest.MonkeyPatch) -> None:
    yield
    monkeypatch.delenv("AUTOSCALER_EXPECTED_WSL_DISTRO", raising=False)
    for env_name, *_rest in NUMERIC_ENV_CASES:
        monkeypatch.delenv(env_name, raising=False)
    importlib.reload(cfg)


class TestEnvFloat:
    def test_default_returned_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("_TEST_FLOAT_VAR", raising=False)
        assert cfg._env_float("_TEST_FLOAT_VAR", 3.14) == pytest.approx(3.14)

    def test_env_value_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_FLOAT_VAR", "1.5")
        assert cfg._env_float("_TEST_FLOAT_VAR", 0.0) == pytest.approx(1.5)

    def test_invalid_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_FLOAT_VAR", "not-a-number")
        with pytest.raises(ValueError, match="_TEST_FLOAT_VAR"):
            cfg._env_float("_TEST_FLOAT_VAR", 9.9)

    def test_negative_env_raises_when_minimum_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_FLOAT_VAR", "-0.1")
        with pytest.raises(ValueError, match="_TEST_FLOAT_VAR"):
            cfg._env_float("_TEST_FLOAT_VAR", 9.9, minimum=0.0)


class TestEnvInt:
    def test_default_returned_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("_TEST_INT_VAR", raising=False)
        assert cfg._env_int("_TEST_INT_VAR", 42) == 42

    def test_env_value_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_INT_VAR", "7")
        assert cfg._env_int("_TEST_INT_VAR", 0) == 7

    def test_invalid_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_INT_VAR", "bad")
        with pytest.raises(ValueError, match="_TEST_INT_VAR"):
            cfg._env_int("_TEST_INT_VAR", 5)

    def test_negative_env_raises_when_minimum_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TEST_INT_VAR", "-1")
        with pytest.raises(ValueError, match="_TEST_INT_VAR"):
            cfg._env_int("_TEST_INT_VAR", 5, minimum=0)


def test_constants_have_expected_types() -> None:
    assert isinstance(cfg.CPU_HIGH, float)
    assert isinstance(cfg.CPU_LOW, float)
    assert isinstance(cfg.MEM_HIGH, float)
    assert isinstance(cfg.DISK_HIGH, float)
    assert isinstance(cfg.DISK_MIN_FREE_GB, float)
    assert isinstance(cfg.LOAD_PER_CORE, float)
    assert isinstance(cfg.SUSTAIN_SECS, int)
    assert isinstance(cfg.POLL_SECONDS, int)
    assert isinstance(cfg.MIN_ONLINE, int)
    assert isinstance(cfg.RECOVERY_MIN_ONLINE, int)
    assert isinstance(cfg.MAX_STEP, int)
    assert isinstance(cfg.DRY_RUN, bool)
    assert isinstance(cfg.HOSTNAME, str)


def test_load_per_core_default_is_1_2() -> None:
    result = cfg._env_float("AUTOSCALER_LOAD_PER_CORE_UNSET_TEST", 1.2)
    assert result == pytest.approx(1.2)


def test_expected_wsl_distro_is_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSCALER_EXPECTED_WSL_DISTRO", raising=False)
    importlib.reload(cfg)
    assert cfg.AUTOSCALER_EXPECTED_WSL_DISTRO == ""


@pytest.mark.parametrize("distro", ["Ubuntu-22.04", "WSL", "Debian_12"])
def test_expected_wsl_distro_accepts_names(monkeypatch: pytest.MonkeyPatch, distro: str) -> None:
    monkeypatch.setenv("AUTOSCALER_EXPECTED_WSL_DISTRO", distro)
    importlib.reload(cfg)
    assert cfg.AUTOSCALER_EXPECTED_WSL_DISTRO == distro


@pytest.mark.parametrize("distro", ["-e", "Ubuntu;touch", "Ubuntu name", "-Ubuntu", " Ubuntu", "Ubuntu "])
def test_expected_wsl_distro_rejects_invalid_names(monkeypatch: pytest.MonkeyPatch, distro: str) -> None:
    monkeypatch.setenv("AUTOSCALER_EXPECTED_WSL_DISTRO", distro)
    with pytest.raises(ValueError, match="AUTOSCALER_EXPECTED_WSL_DISTRO"):
        importlib.reload(cfg)


def test_expected_wsl_distro_name_contract_matches_installer() -> None:
    pattern = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
    root = Path(__file__).resolve().parents[1]
    config_source = (root / "backend" / "autoscaler_config.py").read_text(encoding="utf-8")
    installer_source = (root / "deploy" / "install-autoscaler.sh").read_text(encoding="utf-8")
    assert f'fullmatch(r"{pattern}", value)' in config_source
    assert f"=~ ^{pattern}$" in installer_source
    assert all(re.fullmatch(pattern, value) for value in ["Ubuntu-22.04", "Debian_12", "WSL.1"])
    assert not any(re.fullmatch(pattern, value) for value in ["Ubuntu 24.04", "-Ubuntu", "Ubuntu;bad"])


@pytest.mark.parametrize(("env_name", "attr_name", "raw_value", "expected"), NUMERIC_ENV_CASES)
def test_documented_constants_follow_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    attr_name: str,
    raw_value: str,
    expected: float | int | bool,
) -> None:
    monkeypatch.setenv(env_name, raw_value)
    importlib.reload(cfg)
    assert getattr(cfg, attr_name) == expected


@pytest.mark.parametrize("env_name", [case[0] for case in NUMERIC_ENV_CASES])
def test_negative_numeric_settings_raise_during_reload(monkeypatch: pytest.MonkeyPatch, env_name: str) -> None:
    monkeypatch.setenv(env_name, "-1")
    with pytest.raises(ValueError, match=env_name):
        importlib.reload(cfg)
