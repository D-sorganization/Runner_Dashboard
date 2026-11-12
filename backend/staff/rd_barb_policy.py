"""rd-barb locked-policy boundary and hold-lint (RD #3065, epic #3064).

Holds and safety limits live in a tracked JSON file that only a reviewed PR may
change; it is opened read-only here. :class:`HoldLint` rejects text pairing a
locked identifier (or the words "hold"/"safety limit") with lift, override or
auto-approve language. ``test_hold_lint_canary`` fails CI if either is weakened.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "rd_barb_locked_policy.json"
LIFT_TERMS: tuple[str, ...] = tuple(  # each fragment is load-bearing: the canary drops them one by one
    r"lift(?:s|ed|ing)? overrid(?:e|es|den|ing)|overrode ignor(?:e|es|ed|ing) bypass(?:es|ed|ing)? waiv(?:e|es|ed|ing)"
    r" disabl(?:e|es|ed|ing) skip(?:s|ped|ping)? suspend(?:s|ed|ing)? relax(?:es|ed|ing)? un-?hold(?:s|ing)?"
    r" auto[-\s]?approv(?:e|es|ed|al|ing) auto[-\s]?ok(?:ed|'d)? no\slonger\sappl(?:y|ies)".split()
)
_GENERIC = re.compile(r"\b(?:holds?|safety[- ]limits?)\b", re.IGNORECASE)


class LockedPolicyError(RuntimeError):
    """The locked-policy file is missing or malformed; callers must fail closed."""


@dataclass(frozen=True)
class LockedPolicy:
    ids: tuple[str, ...]
    aliases: tuple[str, ...]

    def touches(self, *values: str) -> str | None:
        """Return the first locked id or alias found in any of ``values``."""
        haystack = " ".join(values).lower()
        return next((name for name in self.ids + self.aliases if name.lower() in haystack), None)


def _entries(data: object, key: str) -> list[dict[str, Any]]:
    items = data.get(key) if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise LockedPolicyError(f"locked policy needs a non-empty {key!r} list")
    if not all(isinstance(item, dict) and str(item.get("id") or "").strip() for item in items):
        raise LockedPolicyError(f"every {key!r} entry needs an id")
    return items


def load_locked_policy(path: Path = DEFAULT_POLICY_PATH) -> LockedPolicy:
    """Read the locked policy through a read-only open. Raises :class:`LockedPolicyError`."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise LockedPolicyError(f"cannot read locked policy: {exc}") from exc
    entries = _entries(data, "holds") + _entries(data, "safety_limits")
    aliases = [a for e in entries for a in (e.get("aliases") or []) if isinstance(a, str) and a.strip()]
    return LockedPolicy(ids=tuple(str(e["id"]).strip() for e in entries), aliases=tuple(a.strip() for a in aliases))


class HoldLint:
    """Rejects text that would lift, override or auto-approve a locked entry."""

    def __init__(self, policy: LockedPolicy, terms: tuple[str, ...] = LIFT_TERMS) -> None:
        if not terms:
            raise ValueError("hold-lint needs at least one lift term")
        self._policy = policy
        self._lift = re.compile(r"(?<![\w-])(?:" + "|".join(terms) + r")(?!\w)", re.IGNORECASE)

    def violation(self, text: str) -> str | None:
        """Return the refusal reason for ``text``, or ``None`` when it is allowed."""
        lift = self._lift.search(text)
        if not lift:
            return None
        generic = _GENERIC.search(text)
        target = self._policy.touches(text) or (generic.group(0) if generic else None)
        return f"hold-lint: {lift.group(0)!r} applied to locked entry {target!r}" if target else None
