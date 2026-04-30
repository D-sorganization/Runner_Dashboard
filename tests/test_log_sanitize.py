"""Tests for the shared log-injection sanitizer (issue #404 DRY consolidation).

These tests cover the contract used by both ``security.sanitize_log_value``
and the dispatch router which now re-exports the same helper.
"""

from __future__ import annotations

from routers.dispatch import _sanitize_log_value as dispatch_sanitize
from security import sanitize_log_value


def test_sanitize_log_value_passes_safe_text() -> None:
    """Plain ASCII text is returned unchanged."""
    assert sanitize_log_value("hello world") == "hello world"


def test_sanitize_log_value_escapes_newline() -> None:
    """Embedded newlines are escaped to prevent log injection."""
    assert sanitize_log_value("line1\nline2") == "line1\\nline2"


def test_sanitize_log_value_escapes_carriage_return() -> None:
    """Embedded carriage returns are escaped."""
    assert sanitize_log_value("a\rb") == "a\\rb"


def test_sanitize_log_value_escapes_tab() -> None:
    """Embedded tabs are escaped."""
    assert sanitize_log_value("a\tb") == "a\\tb"


def test_sanitize_log_value_handles_all_three_escapes() -> None:
    """Mixed control characters are all escaped together."""
    assert sanitize_log_value("a\nb\rc\td") == "a\\nb\\rc\\td"


def test_sanitize_log_value_truncates_to_200_chars() -> None:
    """Long values are truncated to the 200-character limit."""
    raw = "x" * 500
    result = sanitize_log_value(raw)
    assert len(result) == 200
    assert result == "x" * 200


def test_sanitize_log_value_empty_string() -> None:
    """The empty string is returned unchanged."""
    assert sanitize_log_value("") == ""


def test_dispatch_router_reuses_canonical_helper() -> None:
    """The dispatch router exposes the same callable as the security module."""
    assert dispatch_sanitize is sanitize_log_value
