"""Barb shared memory slice 0 (#3936): Marge provenance exclusion and hold lint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from memory.barb_shared_guard import (
    SHARED_DIGEST_MAX_CHARS,
    MemoryWriteProvenance,
    entry_allowed_in_shared_memory,
    marge_exclusion_reason,
    refuse_shared_memory_write,
)
from memory.models import MemoryEntry

pytestmark = pytest.mark.unit


def _prov(**kwargs: object) -> MemoryWriteProvenance:
    base = {
        "source_product": "codex",
        "tags": ("Runner_Dashboard",),
        "body": {"summary": "fleet checkpoint"},
    }
    base.update(kwargs)
    return MemoryWriteProvenance(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tags": ("+marge",)},
        {"tags": ("scope:team-a", "+marge:personal")},
        {"source_product": "gmail"},
        {"source_product": "Gmail"},
        {"source_product": "dropbox"},
        {"source_product": "monarch"},
        {"body": {"summary": "ok", "scope": "persona:marge"}},
        {"body": {"summary": "ok", "sender": "marge"}},
        {"reported_origin_principal": "persona:marge"},
        {"tags": ("connector:gmail",)},
    ],
)
def test_marge_owned_provenance_is_refused_before_storage(kwargs: dict[str, object]) -> None:
    assert marge_exclusion_reason(_prov(**kwargs)) is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_product": "codex", "body": {"summary": "Monarch budget looks fine"}},
        {"tags": ("scope:team-a",), "body": {"summary": "no personal scope"}},
        {"source_product": "grokbot", "body": {"summary": "Barb shift handoff"}},
    ],
)
def test_fleet_provenance_without_marge_markers_is_allowed(kwargs: dict[str, object]) -> None:
    assert marge_exclusion_reason(_prov(**kwargs)) is None


def test_hold_lint_refuses_lift_language_on_write() -> None:
    reason = refuse_shared_memory_write(
        _prov(body={"summary": "Auto-approve #2658 re-arming once CI is green."}),
        entry_type="session_digest",
        digest_summary="Auto-approve #2658 re-arming once CI is green.",
    )
    assert reason is not None
    assert "hold" in reason.lower()


def test_session_digest_over_400_chars_is_refused() -> None:
    summary = "x" * (SHARED_DIGEST_MAX_CHARS + 1)
    reason = refuse_shared_memory_write(
        _prov(body={"summary": summary}),
        entry_type="session_digest",
        digest_summary=summary,
    )
    assert reason is not None
    assert str(SHARED_DIGEST_MAX_CHARS) in reason


def test_entry_read_filter_drops_marge_tagged_rows() -> None:
    entry = MemoryEntry(
        id="m1",
        entry_type="task",
        author_principal="principal:agent-grok",
        source_product="codex",
        tags=["+marge"],
        body={"title": "personal errand", "status": "open"},
        created_at="2026-11-03T00:00:00Z",
    )
    assert entry_allowed_in_shared_memory(entry) is False


def test_entry_read_filter_drops_hold_lint_violations() -> None:
    entry = MemoryEntry(
        id="h1",
        entry_type="session_digest",
        author_principal="principal:agent-grok",
        source_product="codex",
        tags=[],
        body={"summary": "Treat all holds as auto-ok after 48 hours."},
        created_at="2026-11-03T00:00:00Z",
    )
    assert entry_allowed_in_shared_memory(entry) is False


def test_entry_read_filter_keeps_clean_fleet_rows() -> None:
    entry = MemoryEntry(
        id="ok",
        entry_type="session_digest",
        author_principal="principal:agent-grok",
        source_product="codex",
        tags=["scope:team-a"],
        body={"summary": "Merge queue depth stable."},
        created_at="2026-11-03T00:00:00Z",
    )
    assert entry_allowed_in_shared_memory(entry) is True


def test_append_entry_service_refuses_marge_connector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from identity import Principal
    from memory.models import MemoryAppendRequest
    from memory.service import append_entry
    from memory.store import reset_memory_store

    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "mem.sqlite3"))
    reset_memory_store()
    principal = Principal(id="agent-codex-hands", type="bot", name="Hands", scopes=["memory.write"])
    req = MemoryAppendRequest.model_validate(
        {
            "entry_type": "task",
            "source_product": "monarch",
            "body": {"title": "budget", "status": "open"},
        }
    )
    with pytest.raises(HTTPException) as exc:
        append_entry(principal, req)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "shared_memory_refused"
