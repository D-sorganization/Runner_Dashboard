"""Private Mode classification and overlay flag (issue #3250, epic #3249).

Policy and repo maps stay in the private overlay; this module defines the
public-safe ``Level`` lattice, parses the ``private_mode`` overlay block, and
enforces the safe-disable contract (flag off -> behaviour unchanged).

Callers read settings via :func:`read_private_mode` (LoD); they must not read
``RD_OVERLAY_PATH`` or parse overlay JSON themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from overlay_contract import OverlayError, get_overlay

_LEVEL_NAMES = ("PUBLIC", "INTERNAL", "SENSITIVE")


class PrivateModeError(ValueError):
    """Invalid level or use of private-mode APIs while the flag is off."""


class Level(IntEnum):
    """Total order: PUBLIC < INTERNAL < SENSITIVE (max-join uses the greater level)."""

    PUBLIC = 0
    INTERNAL = 1
    SENSITIVE = 2


LEVEL_SEMANTICS: dict[Level, str] = {
    Level.PUBLIC: "Default fleet operation; shared surfaces use normal redaction rules.",
    Level.INTERNAL: (
        "Pattern redaction applies; no metadata-only projection to shared sinks; "
        "approved cloud providers may be used when private_mode is enabled."
    ),
    Level.SENSITIVE: (
        "When private_mode is enabled, only approved on-prem or subscription-local "
        "providers (Claude and Codex per ADR-0009) may run work; cloud agents are "
        "not admitted."
    ),
}


def parse_level(name: str) -> Level:
    """Parse a classification level name; unknown values fail closed."""
    if not isinstance(name, str) or not name:
        raise PrivateModeError("unknown level: value must be a non-empty string")
    key = name.strip().upper()
    if key not in _LEVEL_NAMES:
        raise PrivateModeError(f"unknown level: {name!r}")
    return Level[key]


def max_level(left: Level, right: Level) -> Level:
    """Return the more restrictive (greater) of two levels."""
    return left if left >= right else right


@dataclass(frozen=True)
class PrivateModeSettings:
    """Overlay ``private_mode`` block; absent keys mean disabled / off."""

    enabled: bool
    metric_route_templating_enabled: bool

    @staticmethod
    def disabled() -> PrivateModeSettings:
        return PrivateModeSettings(enabled=False, metric_route_templating_enabled=False)


_METRIC_KEYS: frozenset[str] = frozenset({"enabled"})


def parse_private_mode_block(raw: object) -> PrivateModeSettings:
    """Validate the optional ``private_mode`` overlay object."""
    if raw is None:
        return PrivateModeSettings.disabled()
    if not isinstance(raw, dict):
        raise OverlayError("overlay private_mode: must be an object")
    if not raw:
        return PrivateModeSettings.disabled()
    if "enabled" in raw and not isinstance(raw["enabled"], bool):
        raise OverlayError("overlay private_mode: key 'enabled' must be of type bool")
    unknown_root = sorted(set(raw) - {"enabled", "metric_route_templating"})
    if unknown_root:
        raise OverlayError(f"overlay private_mode: unknown key(s): {', '.join(unknown_root)}")
    enabled = bool(raw.get("enabled", False))
    metric_block = raw.get("metric_route_templating")
    if metric_block is None:
        return PrivateModeSettings(enabled=enabled, metric_route_templating_enabled=False)
    if not isinstance(metric_block, dict):
        raise OverlayError("overlay private_mode.metric_route_templating: must be an object")
    unknown_metric = sorted(set(metric_block) - _METRIC_KEYS)
    if unknown_metric:
        raise OverlayError(
            f"overlay private_mode.metric_route_templating: unknown key(s): {', '.join(unknown_metric)}"
        )
    metric_val = metric_block.get("enabled", False)
    if not isinstance(metric_val, bool):
        raise OverlayError(
            "overlay private_mode.metric_route_templating: key 'enabled' must be of type bool"
        )
    return PrivateModeSettings(enabled=enabled, metric_route_templating_enabled=metric_val)


def private_mode_active(settings: PrivateModeSettings) -> bool:
    return settings.enabled


def effective_default_level(settings: PrivateModeSettings) -> Level:
    """When the flag is off, every surface defaults to PUBLIC."""
    return Level.PUBLIC


def require_private_mode_active(settings: PrivateModeSettings) -> None:
    """Precondition for PM>0 entry points that mutate sensitive lanes."""
    if not private_mode_active(settings):
        raise PrivateModeError("private_mode is disabled")


def read_private_mode(env: Mapping[str, str] | None = None) -> PrivateModeSettings:
    """The only supported way to read the ``private_mode`` flag from the overlay."""
    overlay = get_overlay(env)
    if overlay is None:
        return PrivateModeSettings.disabled()
    return overlay.private_mode
