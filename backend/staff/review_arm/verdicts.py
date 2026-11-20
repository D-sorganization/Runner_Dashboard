"""Verdicts from finished code-reviewer staff runs (armd.py / review_ingest.py parity).

A run's verdict comes from its recorded outcome. When the hub's post-run step
crashed after the reviewer printed its verdict (status ``failed``, no outcome),
the verdict is recovered from the run's events, as armd.py did. Pure functions:
the caller supplies the run status, outcome and events from the RunStore.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_VERDICT = r"review\s+(approve|changes|escalate)(?:\s+#(\d+))?(?:\s+@([0-9a-fA-F]{7,40}))?"
# The stored run outcome ("review approve #7 @abc1234") or a STAFF_RESULT line in it.
_OUTCOME_RE = re.compile(r"\A\s*(?:STAFF_RESULT:\s*)?" + _VERDICT, re.IGNORECASE)
# Recovery from event text trusts only the reviewer's STAFF_RESULT contract line.
_RESULT_RE = re.compile(r"STAFF_RESULT:\s*" + _VERDICT, re.IGNORECASE)
_NEGATED_RE = re.compile(r"\b(?:no|zero|without|none|0)\b[^\[]*\[BLOCKING\]", re.IGNORECASE)
PENDING_STATUSES = frozenset({"", "queued", "running", "preparing", "pending", "starting"})
MAX_TEXT = 60_000


def count_blocking(text: str) -> int:
    """Number of lines that carry a ``[BLOCKING]`` finding; "No [BLOCKING] findings" is none."""
    return sum(1 for line in (text or "").splitlines() if "[BLOCKING]" in line and not _NEGATED_RE.search(line))


@dataclass(frozen=True)
class RunOutcome:
    """``kind``: pending | verdict | failed_no_verdict | no_verdict."""

    kind: str
    verdict: str = ""
    blocking: int = 0
    recovered: bool = False
    verdict_head: str = ""
    text: str = ""


def _final_text(events: Sequence[Mapping[str, Any]]) -> str:
    texts = [str(e.get("text") or "") for e in events if e.get("kind") in {"result", "assistant"}]
    for text in reversed(texts):
        if "STAFF_RESULT" in text:
            return text[-MAX_TEXT:]
    return texts[-1][-MAX_TEXT:] if texts else ""


def extract_outcome(status: str, outcome: str, events: Sequence[Mapping[str, Any]]) -> RunOutcome:
    """Classify one code-reviewer run.

    Post: ``verdict`` kinds carry approve|changes|escalate. A recovered approve
    whose text contains BLOCKING findings is downgraded to changes. A changes
    verdict with a blank review body counts one BLOCKING finding (armd: "verdict
    text EMPTY - not treating changes as approve").
    """
    status = (status or "").strip().lower()
    if status in PENDING_STATUSES:
        return RunOutcome("pending")
    text = _final_text(events)
    match = _OUTCOME_RE.search(outcome or "")
    recovered = False
    if match is None and status == "failed":
        matches = list(_RESULT_RE.finditer(text))
        match = matches[-1] if matches else None
        recovered = match is not None
    if match is None:
        return RunOutcome("failed_no_verdict" if status == "failed" else "no_verdict", text=text)
    verdict = match.group(1).lower()
    blocking = count_blocking(text)
    if verdict == "approve" and blocking:
        verdict = "changes"
    if verdict == "changes" and not text.strip():
        blocking = max(blocking, 1)
    head = (match.group(3) or "").lower()
    return RunOutcome("verdict", verdict, blocking, recovered, head, text)
