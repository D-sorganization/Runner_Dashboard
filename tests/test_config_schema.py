from __future__ import annotations  # noqa: E402

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import config_schema  # noqa: E402
import pytest  # noqa: E402

# ---------------------------------------------------------------------------
# validate_agent_remediation_config
# ---------------------------------------------------------------------------


def test_agent_remediation_empty_is_valid() -> None:
    result = config_schema.validate_agent_remediation_config({})
    assert result == {}


def test_agent_remediation_valid_policy() -> None:
    data = {"policy": {"max_attempts_per_fingerprint": 5}}
    result = config_schema.validate_agent_remediation_config(data)
    assert result == data


def test_agent_remediation_max_attempts_out_of_range() -> None:
    with pytest.raises(ValueError, match="max_attempts_per_fingerprint"):
        config_schema.validate_agent_remediation_config(
            {"policy": {"max_attempts_per_fingerprint": 999}}
        )


def test_agent_remediation_secret_key_rejected() -> None:
    with pytest.raises(ValueError, match="token"):
        config_schema.validate_agent_remediation_config({"token": "abc123"})


# ---------------------------------------------------------------------------
# validate_runner_schedule_config
# ---------------------------------------------------------------------------


def test_runner_schedule_valid() -> None:
    data = {"enabled": True, "default_count": 4}
    result = config_schema.validate_runner_schedule_config(data)
    assert result == data


def test_runner_schedule_negative_default_count() -> None:
    with pytest.raises(ValueError, match="default_count"):
        config_schema.validate_runner_schedule_config({"default_count": -1})


def test_runner_schedule_too_large_default_count() -> None:
    with pytest.raises(ValueError, match="default_count"):
        config_schema.validate_runner_schedule_config({"default_count": 99})


# ---------------------------------------------------------------------------
# atomic_write_json
# ---------------------------------------------------------------------------


def test_atomic_write_json(tmp_path: Path) -> None:
    target = tmp_path / "test.json"
    config_schema.atomic_write_json(target, {"key": "value"})
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {"key": "value"}


# ---------------------------------------------------------------------------
# safe_read_json
# ---------------------------------------------------------------------------


def test_safe_read_json_nonexistent(tmp_path: Path) -> None:
    result = config_schema.safe_read_json(
        tmp_path / "nonexistent.json", {"default": True}
    )
    assert result == {"default": True}


def test_safe_read_json_corrupt(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("not valid json {{{", encoding="utf-8")
    result = config_schema.safe_read_json(corrupt, {"default": True})
    assert result == {"default": True}
