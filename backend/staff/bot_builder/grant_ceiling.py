"""Locked grant ceiling — readable by the builder, writable only via reviewed PR."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CEILING_PATH = Path(__file__).resolve().parents[3] / "config" / "bot_builder_grant_ceiling.json"


class GrantCeilingError(RuntimeError):
    """Grant ceiling file is missing or malformed; fail closed."""


@dataclass(frozen=True)
class GrantCeiling:
    approved_providers: frozenset[str]
    allowed_capabilities: frozenset[str]
    household_capabilities: frozenset[str]


def _non_empty_str_list(data: dict[str, Any], key: str) -> list[str]:
    raw = data.get(key)
    if not isinstance(raw, list) or not raw:
        raise GrantCeilingError(f"grant ceiling needs a non-empty {key!r} list")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise GrantCeilingError(f"every {key!r} entry must be a non-empty string")
        out.append(item.strip())
    return out


def load_grant_ceiling(path: Path = DEFAULT_CEILING_PATH) -> GrantCeiling:
    """Read the grant ceiling through a read-only open."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise GrantCeilingError(f"cannot read grant ceiling: {exc}") from exc
    if not isinstance(data, dict):
        raise GrantCeilingError("grant ceiling must be an object")
    return GrantCeiling(
        approved_providers=frozenset(_non_empty_str_list(data, "approved_providers")),
        allowed_capabilities=frozenset(_non_empty_str_list(data, "allowed_capabilities")),
        household_capabilities=frozenset(_non_empty_str_list(data, "household_capabilities")),
    )
