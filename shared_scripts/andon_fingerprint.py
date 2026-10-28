"""Andon error normalization and fingerprinting (#2901).

Contract fields follow Repository_Management #2454 (A1); this module does not
embed the schema document.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_REQUIRED_KEYS = ("repo", "workflow", "check", "file", "error")
_STRING_KEYS = _REQUIRED_KEYS

_SHA_TOKEN = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
_LINE_COL = re.compile(r"(?i)(line|col|column|at line)\s*[:#]?\s*\d+")
_LINE_COL_PAIR = re.compile(r"\b\d+:\d+\b")
_HEX_ADDR = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_RUNNER = re.compile(r"(?i)\brunner[\s_-]*[a-z0-9][a-z0-9._-]{0,48}\b")
_TEMP_PATH = re.compile(r"(?i)(?:/tmp/|/var/tmp/|\\temp\\|\\tmp\\|/private/var/folders/)[^\s:'\"]+")
_PLACEHOLDER_SHA = "<sha>"
_PLACEHOLDER_TS = "<ts>"
_PLACEHOLDER_LOC = "<loc>"
_PLACEHOLDER_ADDR = "<addr>"
_PLACEHOLDER_RUNNER = "<runner>"
_PLACEHOLDER_TEMP = "<temp>"


class AndonSchemaError(ValueError):
    """Raised when an andon payload violates the A1 contract."""


@dataclass(frozen=True)
class FingerprintResult:
    """Stable fingerprint plus audit material."""

    fingerprint: str
    raw_signature: str
    normalized_error: str


def _require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AndonSchemaError(f"{key} must be a non-empty string")
    return value.strip()


def validate_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    """Validate required A1 fields; return a shallow copy of string fields."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    out: dict[str, str] = {}
    for key in _STRING_KEYS:
        out[key] = _require_str(payload, key)
    return out


def normalize_error_text(error: str) -> str:
    """Strip volatile tokens from free-text CI error output."""
    if not isinstance(error, str):
        raise TypeError("error must be str")
    text = error.strip()
    if not text:
        raise ValueError("error must be non-empty")
    text = _TEMP_PATH.sub(_PLACEHOLDER_TEMP, text)
    text = _RUNNER.sub(_PLACEHOLDER_RUNNER, text)
    text = _ISO_TS.sub(_PLACEHOLDER_TS, text)
    text = _SHA_TOKEN.sub(_PLACEHOLDER_SHA, text)
    text = _HEX_ADDR.sub(_PLACEHOLDER_ADDR, text)
    text = _LINE_COL.sub(_PLACEHOLDER_LOC, text)
    text = _LINE_COL_PAIR.sub(_PLACEHOLDER_LOC, text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(payload: Mapping[str, Any]) -> dict[str, str]:
    """Return an A1-shaped payload with ``normalized_error`` (idempotent)."""
    base = validate_payload(payload)
    normalized_error = normalize_error_text(base["error"])
    return {**base, "normalized_error": normalized_error}


def raw_signature(workflow: str, check: str, file: str, normalized_error: str) -> str:
    return f"{workflow}|{check}|{file}|{normalized_error}"


def fingerprint(payload: Mapping[str, Any]) -> FingerprintResult:
    """Hash the stable andon signature; retain the raw signature for audit."""
    norm = normalize(payload)
    sig = raw_signature(norm["workflow"], norm["check"], norm["file"], norm["normalized_error"])
    digest = hashlib.sha256(sig.encode("utf-8")).hexdigest()
    return FingerprintResult(fingerprint=digest, raw_signature=sig, normalized_error=norm["normalized_error"])
