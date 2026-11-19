"""Fleet efficiency helpers (#4562): wake coalescing, lane brief cap, planned reset thresholds."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WAKE_COALESCE_SEC = 300
MIN_WAKE_COALESCE_SEC = 300
MAX_WAKE_COALESCE_SEC = 600
DEFAULT_BRIEF_LINE_CAP = 6
DEFAULT_MIN_TURNS = 25
DEFAULT_MIN_TOKENS_PER_TURN = 150_000

_STATUS = re.compile(r"^Status:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RESULT = re.compile(r"^Result:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_EVIDENCE = re.compile(r"^Evidence:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_EVIDENCE_PATH = re.compile(r"^Evidence path:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


class BriefValidationError(ValueError):
    """Lane completion brief failed the fleet 6-line Status/Result/Evidence contract."""


def wake_coalesce_seconds() -> int:
    """Configurable 5–10 minute wake batching window (default 5 minutes)."""
    raw = (os.environ.get("FLEET_WAKE_COALESCE_SEC") or "").strip()
    if not raw:
        return DEFAULT_WAKE_COALESCE_SEC
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WAKE_COALESCE_SEC
    return min(MAX_WAKE_COALESCE_SEC, max(MIN_WAKE_COALESCE_SEC, value))


@dataclass
class WakeCoalescer:
    """Batch lane-finished and CI wake signals per recipient within ``window_sec``."""

    window_sec: int
    _last_wake: dict[tuple[str, str], float] = field(default_factory=dict)

    def should_deliver_wake(self, kind: str, recipient: str, *, now: float | None = None) -> bool:
        ts = time_now(now)
        key = (kind.strip().lower(), recipient.strip().lower())
        last = self._last_wake.get(key)
        if last is None:
            return True
        return (ts - last) >= float(self.window_sec)

    def record_wake(self, kind: str, recipient: str, *, now: float | None = None) -> None:
        ts = time_now(now)
        key = (kind.strip().lower(), recipient.strip().lower())
        self._last_wake[key] = ts


def time_now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def format_lane_brief(
    *,
    status: str,
    result: str,
    evidence: str,
    evidence_path: str,
) -> str:
    """Render a <=6-line Status / Result / Evidence brief with an evidence link or path."""
    lines = [
        f"Status: {status.strip()}",
        f"Result: {result.strip()}",
        f"Evidence: {evidence.strip()}",
        f"Evidence path: {evidence_path.strip()}",
    ]
    if len(lines) > DEFAULT_BRIEF_LINE_CAP:
        raise BriefValidationError(f"brief formatter produced {len(lines)} lines; cap is {DEFAULT_BRIEF_LINE_CAP}")
    return "\n".join(lines)


def validate_lane_brief(text: str, *, evidence_path: str | None = None) -> dict[str, str]:
    """Validate inter-agent completion text: <=6 lines, required fields, evidence path link."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) > DEFAULT_BRIEF_LINE_CAP:
        raise BriefValidationError(f"brief exceeds {DEFAULT_BRIEF_LINE_CAP}-line cap ({len(lines)} lines)")
    status = _match_one(_STATUS, text, "Status")
    result = _match_one(_RESULT, text, "Result")
    evidence = _match_one(_EVIDENCE, text, "Evidence")
    path = _match_one(_EVIDENCE_PATH, text, "Evidence path")
    if evidence_path is not None and path != evidence_path.strip():
        raise BriefValidationError("Evidence path line does not match required evidence_path")
    if not path:
        raise BriefValidationError("Evidence path is required (full logs live outside the brief)")
    return {"status": status, "result": result, "evidence": evidence, "evidence_path": path}


def _match_one(pattern: re.Pattern[str], text: str, label: str) -> str:
    match = pattern.search(text)
    if not match:
        raise BriefValidationError(f"missing {label} line")
    return match.group(1).strip()


def planned_reset_thresholds() -> dict[str, Any]:
    return {
        "min_turns": DEFAULT_MIN_TURNS,
        "min_tokens_per_turn": DEFAULT_MIN_TOKENS_PER_TURN,
        "post_milestone": True,
        "pre_reset_sync": "Sync RD turnover/inventory before mandatory context reset",
    }


def evaluate_planned_reset(
    *,
    turn_count: int,
    tokens_last_turn: int,
    post_milestone: bool = False,
) -> dict[str, Any]:
    thresholds = planned_reset_thresholds()
    reasons: list[str] = []
    if turn_count >= int(thresholds["min_turns"]):
        reasons.append("turns")
    if tokens_last_turn >= int(thresholds["min_tokens_per_turn"]):
        reasons.append("tokens_per_turn")
    if post_milestone and thresholds["post_milestone"]:
        reasons.append("post_milestone")
    return {
        "recommended": bool(reasons),
        "reasons": reasons,
        "thresholds": thresholds,
        "turn_count": turn_count,
        "tokens_last_turn": tokens_last_turn,
    }


def fleet_efficiency_metadata() -> dict[str, Any]:
    return {
        "wake_coalesce_sec": wake_coalesce_seconds(),
        "brief_line_cap": DEFAULT_BRIEF_LINE_CAP,
        "planned_reset": planned_reset_thresholds(),
    }


def enrich_lane_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {**receipt, "fleet_efficiency": fleet_efficiency_metadata()}


def enrich_hub_session(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "fleet_efficiency": fleet_efficiency_metadata()}