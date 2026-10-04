"""Reasoning effort per dispatch kind (USE-1, issue #1865).

RD-3 (#1848) picks the model tier for a dispatch; this module picks how hard
that model should think. A lint fix or a templated issue body does not need the
effort a design task does, so every dispatch kind maps to one of three levels:

- ``ci_fix:lint`` -> low, ``ci_fix:test`` -> medium,
- ``ci_fix:conflict`` -> low (merge main into a dequeued PR, #1879),
- ``implementation`` -> medium, ``design`` -> high,
- ``expand_epic_children`` -> low (templated, well-specified writing).

Unknown kinds fall back to :data:`DEFAULT_EFFORT` (medium), never to high:
an unclassified dispatch keeps today's behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal, cast, get_args

Effort = Literal["low", "medium", "high"]

EFFORT_LEVELS: Final[tuple[str, ...]] = get_args(Effort)
DEFAULT_EFFORT: Final[Effort] = "medium"
EXPAND_EPIC_CHILDREN: Final = "expand_epic_children"

DISPATCH_KIND_EFFORT: Final[Mapping[str, Effort]] = MappingProxyType(
    {
        "ci_fix:lint": "low",
        "ci_fix:test": "medium",
        "ci_fix:conflict": "low",
        "ci_fix:escalated": "high",
        "implementation": "medium",
        "pr": "medium",
        "design": "high",
        EXPAND_EPIC_CHILDREN: "low",
    }
)


def validate_effort(value: object) -> Effort:
    """Return ``value`` as an :data:`Effort`, or raise ``ValueError``.

    Precondition: none (boundary validator for untrusted input).
    Postcondition: the result is one of :data:`EFFORT_LEVELS`.
    """
    if not isinstance(value, str) or value not in EFFORT_LEVELS:
        raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}; got {value!r}")
    return cast(Effort, value)


def normalize_dispatch_kind(dispatch_kind: str) -> str:
    """Lower-case and strip a dispatch kind (``" CI_FIX:Lint "`` -> ``"ci_fix:lint"``)."""
    if not isinstance(dispatch_kind, str):
        raise TypeError("dispatch_kind must be a string")
    return dispatch_kind.strip().lower()


def resolve_effort(dispatch_kind: str, override: str | None = None) -> Effort:
    """Resolve the reasoning effort for a dispatch kind.

    Precondition: ``dispatch_kind`` is a string; ``override``, when given, is
    one of :data:`EFFORT_LEVELS` (``ValueError`` otherwise).
    Postcondition: returns one of :data:`EFFORT_LEVELS`; an explicit override
    wins, then the per-kind mapping, then :data:`DEFAULT_EFFORT`.
    """
    kind = normalize_dispatch_kind(dispatch_kind)
    if override is not None:
        return validate_effort(override)
    effort = DISPATCH_KIND_EFFORT.get(kind, DEFAULT_EFFORT)
    assert effort in EFFORT_LEVELS  # noqa: S101 - invariant of the mapping above
    return effort
