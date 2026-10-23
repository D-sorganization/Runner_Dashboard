"""Memory redaction includes GitHub App tokens."""

from __future__ import annotations

from memory.redact import redact_memory_text


def test_redacts_ghs_token() -> None:
    raw = "token ghs_" + "B" * 30
    out = redact_memory_text(raw)
    assert "ghs_" not in out
    assert "[REDACTED_GITHUB_TOKEN]" in out
