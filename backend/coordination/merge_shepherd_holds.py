"""Canonical merge-shepherd hold labels (#4573).

Pre: callers compare GitHub issue/PR labels case-insensitively.
Post: every merge-shepherd code path uses ``MERGE_SHEPHERD_HOLD_LABELS`` (or a documented subset)
from this module, not a locally defined hold set.
"""

from __future__ import annotations

import enum
import re


class _StrEnum(str, enum.Enum):  # noqa: UP042
    """Python 3.10 compatible StrEnum helper."""

    pass


class MergeShepherdHoldLabel(_StrEnum):
    """Typed GitHub labels that block merge-shepherd arming."""

    BLOCKED = "blocked"
    DO_NOT_AUTOMATE = "do-not-automate"
    DO_NOT_MERGE = "do-not-merge"
    DNM = "dnm"
    DRAIN_HOLD = "drain-hold"
    HOLD = "hold"
    NEEDS_DECISION = "needs-decision"
    NEEDS_DIETER = "needs-dieter"
    NEEDS_OWNER = "needs-owner"
    WIP = "wip"


MERGE_SHEPHERD_HOLD_LABELS: frozenset[str] = frozenset(member.value for member in MergeShepherdHoldLabel)

LOW_RISK_SHEPHERD_BLOCKED_LABELS: frozenset[str] = frozenset(
    {
        MergeShepherdHoldLabel.HOLD.value,
        MergeShepherdHoldLabel.DO_NOT_MERGE.value,
        MergeShepherdHoldLabel.NEEDS_DIETER.value,
    }
)

MERGE_SHEPHERD_HOLD_TITLE_RE = re.compile(
    r"\[hold\]|\(held\)|\(hold\)|^\s*hold\b",
    re.IGNORECASE,
)


def normalize_hold_label(label: str) -> str:
    return str(label).strip().casefold()


def label_is_merge_shepherd_hold(label: str) -> bool:
    return normalize_hold_label(label) in MERGE_SHEPHERD_HOLD_LABELS


def pr_title_indicates_hold(title: str) -> bool:
    return bool(title and MERGE_SHEPHERD_HOLD_TITLE_RE.search(title))