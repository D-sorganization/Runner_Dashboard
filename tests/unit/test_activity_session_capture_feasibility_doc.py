"""Contract tests for Activity T6 Cursor desktop & Antigravity session capture feasibility doc (#2873)."""

from __future__ import annotations

from pathlib import Path

from activity import redact_sensitive_content

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "activity-ingestion.md"


def test_feasibility_note_covers_cursor_and_antigravity_surfaces_and_recommendations() -> None:
    assert DOC_PATH.is_file(), f"missing doc: {DOC_PATH}"
    text = DOC_PATH.read_text(encoding="utf-8")

    # Must never contain private IPs, hostnames, usernames, emails or tokens.
    assert redact_sensitive_content(text) == text

    # Required section and per-app coverage (#2873 acceptance criteria).
    assert "## Cursor Desktop and Antigravity (`agy`) Session Capture Feasibility (#2873)" in text
    assert "### Cursor Desktop / IDE" in text
    assert "### Antigravity (`agy`) / Gemini CLI & IDE" in text

    # Cursor evidence: hooks.json supported for lifecycle metadata; full desktop transcript tailing not feasible.
    assert ".cursor/hooks.json" in text
    assert "not feasible" in text.lower()
    assert "state.vscdb" in text

    # Antigravity evidence: .agents/hooks.json + transcript.jsonl supported surface and follow-up recommendation.
    assert ".agents/hooks.json" in text
    assert "transcript.jsonl" in text
    assert "### Follow-up Adapter Proposals" in text
