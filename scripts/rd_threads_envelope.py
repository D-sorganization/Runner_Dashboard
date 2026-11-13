"""rd-threads message envelope (#3072): the one schema every rd-barb path shares (fields per the issue;
importance/ack_required follow Agent Mail, task_state follows A2A). DbC: :func:`validate` never raises and
lists every problem; :func:`dedupe_key` (SHA-256 of payload + 60 s bucket) is the send Idempotency-Key.
"""

import hashlib
import json
from typing import Any

KINDS = frozenset({"question", "alert", "update", "answer"})
URGENCIES = frozenset({"low", "normal", "high"})
IMPORTANCES = frozenset({"low", "normal", "high", "urgent"})
TASK_STATES = frozenset({"submitted", "working", "input-required", "completed"})
DEDUPE_BUCKET_S = 60
_ENUMS = {"kind": KINDS, "urgency": URGENCIES, "importance": IMPORTANCES, "task_state": TASK_STATES}
_DEFAULT_STATE = {"question": "input-required", "alert": "submitted", "update": "completed", "answer": "completed"}


def _one_line(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value


def _recommended_label(options: Any) -> str | None:
    picks = [o for o in options or [] if isinstance(o, dict) and o.get("recommended") is True]
    return picks[0].get("label") if len(picks) == 1 else None


def with_defaults(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with defaults filled; never overwrites a caller-supplied value."""
    kind = msg.get("kind")
    defaults = {"body": "", "urgency": "normal", "importance": "normal", "ack_required": kind == "question"}
    defaults.update(expiry=None, task_state=_DEFAULT_STATE.get(str(kind), "submitted"))
    if kind == "question":
        defaults["recommended"] = _recommended_label(msg.get("options"))
    return {**defaults, **msg}


def validate(msg: Any) -> list[str]:
    """Return every contract problem with ``msg``; an empty list means valid."""
    if not isinstance(msg, dict):
        return ["envelope must be a JSON object"]
    errors = [f"{f} must be one of {sorted(ok)}" for f, ok in _ENUMS.items() if msg.get(f) not in ok]
    errors += [f"{f} must be one non-empty line" for f in ("from", "to", "summary") if not _one_line(msg.get(f))]
    errors += [] if isinstance(msg.get("ack_required"), bool) else ["ack_required must be a boolean"]
    errors += [] if isinstance(msg.get("body", ""), str) else ["body must be a string"]
    if msg.get("kind") == "alert" and not _one_line(msg.get("recommended_action")):
        errors.append("alert requires a one-line recommended_action")
    return errors + (_question_errors(msg) if msg.get("kind") == "question" else [])


def _question_errors(msg: dict[str, Any]) -> list[str]:
    options = msg.get("options")
    if not isinstance(options, list) or len(options) < 2:
        return ["question requires options: a list of at least 2 {label, recommended?}"]
    errors = [] if all(isinstance(o, dict) and _one_line(o.get("label")) for o in options) else ["bad option label"]
    picks = sum(1 for o in options if isinstance(o, dict) and o.get("recommended") is True)
    if picks != 1:
        errors.append(f"question needs exactly one option with recommended: true (found {picks})")
    elif msg.get("recommended") != _recommended_label(options):
        errors.append("recommended must equal the label of the recommended option")
    if msg.get("task_state") != "input-required":
        errors.append("a question waiting on an answer must have task_state input-required")
    if not _one_line(msg.get("recommendation_reason")):
        errors.append("question requires a one-line recommendation_reason")
    return errors


def dedupe_key(msg: dict[str, Any], now: float) -> str:
    """SHA-256 of the canonical payload (generated ids excluded) plus the 60 s bucket."""
    payload = {k: v for k, v in msg.items() if k not in ("id", "correlation_id")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{canonical}|{int(now // DEDUPE_BUCKET_S)}".encode()).hexdigest()


def seal(msg: dict[str, Any], now: float) -> dict[str, Any]:
    """Fill defaults and derive ``id``/``correlation_id`` from the dedupe key (stable on retry)."""
    key = dedupe_key(out := with_defaults(msg), now)[:24]
    return {"id": f"m_{key}", "correlation_id": f"c_{key}", **out}
