"""Structural tests for assistant chat privacy documentation (issue #428).

The in-app assistant sidebar modules were removed as orphaned code (#1330).
Privacy guarantees for any future assistant surface remain documented in
``docs/privacy.md``; implementation-level source assertions were retired with
the deleted ``AssistantSidebar`` / ``assistantStorage`` modules.
"""

from pathlib import Path


def _read_privacy_doc() -> str:
    repo = Path(__file__).resolve().parents[2]
    return (repo / "docs/privacy.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC 3 — docs/privacy.md exists and documents the feature
# ---------------------------------------------------------------------------


def test_privacy_doc_exists() -> None:
    doc = _read_privacy_doc()
    assert len(doc) > 0


def test_privacy_doc_mentions_save_history_toggle() -> None:
    doc = _read_privacy_doc()
    assert "Save chat history" in doc


def test_privacy_doc_mentions_24h_expiry() -> None:
    doc = _read_privacy_doc()
    assert "24" in doc
