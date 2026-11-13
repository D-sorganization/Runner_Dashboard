"""Andon fingerprint library (#2901)."""

from __future__ import annotations

import pytest

from shared_scripts.andon_fingerprint import (
    AndonSchemaError,
    fingerprint,
    normalize,
    normalize_error_text,
)


def _payload(**overrides: str) -> dict[str, str]:
    base = {
        "repo": "D-sorganization/Runner_Dashboard",
        "workflow": "ci.yml",
        "check": "pytest",
        "file": "tests/test_example.py",
        "error": "AssertionError: boom",
    }
    base.update(overrides)
    return base


def test_normalize_error_is_idempotent() -> None:
    raw = "failed on runner-hub-a at 2026-10-25T12:00:00Z commit abcdef1 line 42:7 /tmp/cache/x"
    once = normalize_error_text(raw)
    twice = normalize_error_text(once)
    assert once == twice
    assert "<sha>" in once and "<ts>" in once and "<runner>" in once and "<loc>" in once


def test_fingerprint_split_same_error_different_volatile_fields() -> None:
    a = fingerprint(
        _payload(
            error="FAILED at abcdef0123456789 line 10 on runner-fleet-2",
        )
    )
    b = fingerprint(
        _payload(
            error="FAILED at fedcba0987654321 line 99 on runner-fleet-9",
        )
    )
    assert a.fingerprint == b.fingerprint
    assert a.normalized_error == b.normalized_error


def test_fingerprint_collision_distinct_root_causes_stay_distinct() -> None:
    a = fingerprint(_payload(error="TypeError: missing argument"))
    b = fingerprint(_payload(error="ValueError: bad value"))
    assert a.fingerprint != b.fingerprint


def test_normalize_payload_matches_schema_and_idempotent() -> None:
    once = normalize(_payload(error="deadbeef1 failed"))
    twice = normalize(once)
    assert once == twice
    assert set(once) >= {"repo", "workflow", "check", "file", "error", "normalized_error"}


def test_validate_payload_rejects_missing_fields() -> None:
    with pytest.raises(AndonSchemaError):
        normalize({"repo": "x", "workflow": "w", "check": "c", "file": "f"})
