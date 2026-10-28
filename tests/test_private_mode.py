"""Contract tests for Private Mode classification levels (issue #3250, epic #3249)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_mode as pm  # noqa: E402


def test_level_is_a_total_order() -> None:
    assert pm.Level.PUBLIC < pm.Level.INTERNAL < pm.Level.SENSITIVE
    assert sorted(pm.Level, reverse=True)[0] is pm.Level.SENSITIVE


def test_absent_private_mode_is_disabled() -> None:
    """Flag absent -> PUBLIC everywhere (the safe-disable contract)."""
    config = pm.parse_private_mode(None)
    assert config == pm.DISABLED
    assert config.enabled is False
    assert config.default_level is pm.Level.PUBLIC


def test_explicit_disabled_round_trips() -> None:
    config = pm.parse_private_mode({"enabled": False, "default_level": "PUBLIC"})
    assert config == pm.DISABLED


def test_enabled_with_internal_default() -> None:
    config = pm.parse_private_mode({"enabled": True, "default_level": "INTERNAL"})
    assert config.enabled is True
    assert config.default_level is pm.Level.INTERNAL


@pytest.mark.parametrize("level_name", ["public", "Sensitive", "TOP_SECRET", "", "PUBLICC"])
def test_unsupported_level_name_is_rejected(level_name: str) -> None:
    with pytest.raises(ValueError, match="unsupported level"):
        pm.parse_private_mode({"enabled": True, "default_level": level_name})


def test_non_object_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        pm.parse_private_mode(["enabled"])


def test_missing_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing required key.*default_level"):
        pm.parse_private_mode({"enabled": True})


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown key.*scope"):
        pm.parse_private_mode({"enabled": True, "default_level": "PUBLIC", "scope": "repo"})


@pytest.mark.parametrize(
    ("field", "value"),
    [("enabled", "true"), ("enabled", 1), ("default_level", True), ("default_level", 0)],
)
def test_wrong_value_type_is_rejected(field: str, value: object) -> None:
    data = {"enabled": True, "default_level": "PUBLIC"}
    data[field] = value
    with pytest.raises(ValueError, match=field):
        pm.parse_private_mode(data)


def test_config_is_immutable() -> None:
    config = pm.parse_private_mode(None)
    with pytest.raises(AttributeError):
        config.enabled = True  # type: ignore[misc]
