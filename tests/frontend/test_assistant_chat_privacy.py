"""Structural tests for the assistant chat privacy controls (issue #428).

These tests read the frontend source file and assert that the key privacy
constructs are present without spinning up a browser.
"""

from pathlib import Path


def _read_sidebar() -> str:
    repo = Path(__file__).resolve().parents[2]
    return (repo / "frontend/src/pages/AssistantSidebar.tsx").read_text(encoding="utf-8")


def _read_storage() -> str:
    repo = Path(__file__).resolve().parents[2]
    return (repo / "frontend/src/lib/assistantStorage.ts").read_text(encoding="utf-8")


def _read_privacy_doc() -> str:
    repo = Path(__file__).resolve().parents[2]
    return (repo / "docs/privacy.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC 1 — "Save chat history" toggle defaults to OFF
# ---------------------------------------------------------------------------


def test_save_history_key_defined() -> None:
    src = _read_storage()
    assert 'saveHistory: "assistant:saveHistory"' in src


def test_save_history_defaults_false() -> None:
    src = _read_sidebar()
    assert "lsGet(ASST_LS.saveHistory, false)" in src


def test_save_history_checkbox_label() -> None:
    src = _read_sidebar()
    assert '"Save chat history"' in src


def test_save_history_header_toggle_present() -> None:
    src = _read_sidebar()
    assert 'title: "Save chat history"' in src
    assert '"aria-label": "Save chat history"' in src


# ---------------------------------------------------------------------------
# AC 2 — Auto-clear after 24 h
# ---------------------------------------------------------------------------


def test_24h_ttl_constant_defined() -> None:
    src = _read_storage()
    assert "ASST_HISTORY_TTL_MS" in src
    assert "24 * 60 * 60 * 1000" in src


def test_transcript_timestamp_key_defined() -> None:
    src = _read_storage()
    assert 'transcriptTimestamp: "assistant:transcript:ts"' in src


def test_ls_load_transcript_checks_expiry() -> None:
    src = _read_storage()
    assert "lsLoadTranscript" in src
    assert "ASST_HISTORY_TTL_MS" in src


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


# ---------------------------------------------------------------------------
# AC 4 — "Clear chat history" button present in sidebar
# ---------------------------------------------------------------------------


def test_clear_chat_history_button_present() -> None:
    src = _read_sidebar()
    assert '"Clear chat history"' in src


def test_clear_button_removes_transcript_from_ls() -> None:
    sidebar = _read_sidebar()
    storage = _read_storage()
    # The clear button delegates to the shared storage helper, and the helper
    # must remove both transcript keys.
    assert "clearAssistantTranscriptHistory()" in sidebar
    assert "localStorage.removeItem(ASST_LS.transcript)" in storage
    assert "localStorage.removeItem(ASST_LS.transcriptTimestamp)" in storage


# ---------------------------------------------------------------------------
# AC 5 — With toggle off, transcript NOT written to localStorage
# ---------------------------------------------------------------------------


def test_transcript_persist_guarded_by_save_history() -> None:
    src = _read_sidebar()
    # The effect that persists the transcript must check saveHistory
    assert "if (!saveHistory)" in src


def test_transcript_not_loaded_when_save_history_off() -> None:
    src = _read_sidebar()
    # Transcript initialised conditionally on saveHistory
    assert "saveHistory ? lsLoadTranscript()" in src
