"""Private Mode classification levels (issue #3250, epic #3249/#3077).

Defines the total-ordered :class:`Level` enum and the ``private_mode``
overlay schema field (#3079). An overlay with no ``private_mode`` key, or no
overlay at all, resolves to :data:`DISABLED` (``enabled=False``,
``default_level=Level.PUBLIC``): classification never diverges from today's
PUBLIC-everywhere behaviour. That is the safe-disable contract this slice
ships; every later Private Mode slice builds on it without changing it.

Level semantics:

* ``PUBLIC`` -- may appear on any surface: hub logs, transcripts, panel
  threads, board posts, metrics, GitHub logs and artifacts. Today's only
  level; unchanged while the flag is off.
* ``INTERNAL`` -- fleet-internal. Stays off public surfaces (public repo
  mirrors, public GitHub issues/PRs/comments, OSS artifacts) but may still
  reach the fleet's own private hub logs, transcripts and board. The level
  for private-but-not-sensitive content; a later slice (PM-1) migrates
  legacy, pre-flag threads here rather than leaving them PUBLIC.
* ``SENSITIVE`` -- must never reach *any* shared surface at all: not hub
  logs, not transcripts, not panel threads, not board posts, not metrics,
  not GitHub logs or artifacts. Confined to private, ephemeral, RD-owned
  infrastructure built out in later slices of this epic.

``Level`` is a total order (``PUBLIC < INTERNAL < SENSITIVE``): joining two
levels for the same content always keeps the more restrictive one.

Validation error messages name keys and reasons only; they never echo the
rejected value, because an operator-supplied ``default_level`` may be a
placeholder for something private.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

_FIELDS: dict[str, type] = {"enabled": bool, "default_level": str}


class Level(IntEnum):
    """A total order of classification levels, least to most restricted."""

    PUBLIC = 0
    INTERNAL = 1
    SENSITIVE = 2


@dataclass(frozen=True)
class PrivateModeConfig:
    """Validated ``private_mode`` overlay field. Construct via :func:`parse_private_mode`."""

    enabled: bool
    default_level: Level


DISABLED = PrivateModeConfig(enabled=False, default_level=Level.PUBLIC)


def parse_private_mode(data: object | None) -> PrivateModeConfig:
    """Validate an optional ``private_mode`` overlay value.

    ``None`` (the key is absent) returns :data:`DISABLED` -- the
    safe-disable contract. Present, it must be an object with exactly
    ``enabled`` (bool) and ``default_level`` (one of :class:`Level`'s names).
    """
    if data is None:
        return DISABLED
    if not isinstance(data, dict):
        raise ValueError("private_mode: must be an object")
    missing = sorted(set(_FIELDS) - set(data))
    unknown = sorted(set(data) - set(_FIELDS))
    if missing:
        raise ValueError(f"private_mode: missing required key(s): {', '.join(missing)}")
    if unknown:
        raise ValueError(f"private_mode: unknown key(s): {', '.join(unknown)}")
    enabled = data["enabled"]
    if not isinstance(enabled, bool):
        raise ValueError("private_mode: key 'enabled' must be of type bool")
    level_name = data["default_level"]
    if not isinstance(level_name, str):
        raise ValueError("private_mode: key 'default_level' must be of type str")
    try:
        level = Level[level_name]
    except KeyError:
        valid = ", ".join(member.name for member in Level)
        raise ValueError(f"private_mode: unsupported level (expected one of {valid})") from None
    return PrivateModeConfig(enabled=enabled, default_level=level)
