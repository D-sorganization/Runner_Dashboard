from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Keys considered sensitive — their presence with a non-empty string value is rejected.
_SECRET_KEYS = frozenset({"token", "password", "secret", "api_key"})


def _check_secret_keys(data: dict[str, Any], depth: int = 0) -> None:
    """Raise ValueError if any secret key has a non-empty string value (2 levels deep)."""
    for key, value in data.items():
        if key in _SECRET_KEYS and isinstance(value, str) and value:
            raise ValueError(f"config must not contain a non-empty '{key}' field")
        if depth < 1 and isinstance(value, dict):
            _check_secret_keys(value, depth + 1)


def _validate_provider_order(provider_order: object) -> None:
    """Validate policy.provider_order is a list of non-empty strings."""
    if not isinstance(provider_order, list):
        raise ValueError("policy.provider_order must be a list")
    for item in provider_order:
        if not isinstance(item, str) or not item:
            raise ValueError(
                "policy.provider_order must be a list of non-empty strings"
            )


def _validate_bounded_int(value: object, field: str, lo: int, hi: int) -> None:
    """Validate that *value* is an int in [lo, hi]."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an int")
    if not (lo <= value <= hi):
        raise ValueError(f"{field} must be between {lo} and {hi}")


def _validate_policy_block(policy: object) -> None:
    """Validate the optional 'policy' sub-dict of an agent_remediation config."""
    if not isinstance(policy, dict):
        raise ValueError("agent_remediation config 'policy' must be a dict")
    _check_secret_keys(policy)

    if (provider_order := policy.get("provider_order")) is not None:
        _validate_provider_order(provider_order)

    if (max_attempts := policy.get("max_attempts_per_fingerprint")) is not None:
        _validate_bounded_int(
            max_attempts, "policy.max_attempts_per_fingerprint", 1, 20
        )

    if (max_daily := policy.get("max_daily_dispatch")) is not None:
        _validate_bounded_int(max_daily, "policy.max_daily_dispatch", 1, 100)


def validate_agent_remediation_config(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a normalized agent_remediation config dict.

    Raises ValueError with a descriptive message on invalid input.
    """
    if not isinstance(data, dict):
        raise ValueError("agent_remediation config must be a dict")

    _check_secret_keys(data)

    if (policy := data.get("policy")) is not None:
        _validate_policy_block(policy)

    return data


def _validate_schedule_entries(schedules: object) -> None:
    """Validate the 'schedules' list inside a runner_schedule config."""
    if not isinstance(schedules, list):
        raise ValueError("runner_schedule 'schedules' must be a list")
    for i, entry in enumerate(schedules):
        if not isinstance(entry, dict):
            raise ValueError(f"runner_schedule schedules[{i}] must be a dict")
        days = entry.get("days")
        if not isinstance(days, list):
            raise ValueError(f"runner_schedule schedules[{i}] must have a 'days' list")


def validate_runner_schedule_config(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a normalized runner_schedule config dict.

    Raises ValueError with a descriptive message on invalid input.
    """
    if not isinstance(data, dict):
        raise ValueError("runner_schedule config must be a dict")

    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("runner_schedule 'enabled' must be a bool")

    if (default_count := data.get("default_count")) is not None:
        _validate_bounded_int(default_count, "runner_schedule 'default_count'", 0, 32)

    if (schedules := data.get("schedules")) is not None:
        _validate_schedule_entries(schedules)

    return data


def validate_usage_sources_config(data: Any) -> Any:
    """Validate usage_sources config (may be dict or list).

    Raises ValueError if the value contains secret keys or is an unsupported type.
    """
    if not isinstance(data, (dict, list)):
        raise ValueError("usage_sources config must be a dict or list")

    if isinstance(data, dict):
        _check_secret_keys(data)
    else:
        for item in data:
            if isinstance(item, dict):
                _check_secret_keys(item)

    return data


def atomic_write_json(path: Path, data: Any) -> None:
    """Write *data* as JSON to *path* atomically via a temp file + os.replace.

    This ensures no partial-write is visible to readers even if the process is
    interrupted mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def safe_read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    """Read JSON from *path*, returning *default* on missing file or parse errors."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return default
