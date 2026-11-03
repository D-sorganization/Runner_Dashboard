"""Decision table for refusing ``gh pr create`` on drained repositories (#3915).

Pure standard library on purpose: the node-side ``gh`` shim (a later #3915
slice) imports this module without the dashboard backend, feeding it the hub's
``GET /api/directives/drain`` response. ``status`` is ``None`` when the hub
could not be reached, which fails open with a warning rather than blocking
every PR fleet-wide.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

EXEMPT_CLASSES = ("ci", "security", "red-main")

# Conventional-commit title prefixes that mark an exempt class: ``ci: ...``,
# ``ci(runner): ...``, ``fix(security): ...``, ``red-main: ...``.
_TITLE_EXEMPT = re.compile(r"^\s*(?:(ci|security|red-main)|fix\((security|red-main)\))(?:\([^)]*\))?!?:", re.IGNORECASE)


@dataclass(frozen=True)
class GuardDecision:
    allow: bool
    reason: str
    message: str


def exempt_class(title: str, labels: Iterable[str]) -> str | None:
    """The exempt class a PR belongs to (label first, then title prefix), else ``None``."""
    normalised = {str(label).strip().lower() for label in labels}
    for cls in EXEMPT_CLASSES:
        if cls in normalised:
            return cls
    match = _TITLE_EXEMPT.match(title or "")
    if match is None:
        return None
    return (match.group(1) or match.group(2)).lower()


def guard_decision(status: Mapping[str, Any] | None, *, title: str, labels: Iterable[str]) -> GuardDecision:
    """Allow or refuse one ``gh pr create`` given the hub's drain status for the target repo."""
    if status is None:
        return GuardDecision(True, "hub_unreachable_fail_open", "drain status unavailable (hub unreachable); allowing")
    if not status.get("drained"):
        return GuardDecision(True, "not_drained", "")
    cls = exempt_class(title, labels)
    if cls is not None:
        return GuardDecision(True, f"exempt:{cls}", f"repository is drained; allowed as an exempt {cls} PR")
    exempt = ", ".join(EXEMPT_CLASSES)
    return GuardDecision(
        False,
        "drained",
        "refusing gh pr create: this repository is in drain mode (an operator directive). "
        f"Only exempt classes ({exempt}) may open PRs; use a matching label or title prefix, "
        "or wait for the drain to lift.",
    )
