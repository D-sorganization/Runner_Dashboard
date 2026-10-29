"""Unit tests for persona role capsule store, Barbara mirror, and SENSITIVE projection (#3295 S5, #3296)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.role_capsule import (
    MAX_CAPSULE_BYTES,
    CapsuleError,
    CapsuleStaleEpochError,
    RoleCapsuleStore,
    emit_shared,
)


def test_save_bob_capsule_mirrors_to_barbara_and_enforces_monotonic_epoch(tmp_path: Path) -> None:
    mirrored: dict[str, dict[str, object]] = {}
    store = RoleCapsuleStore(
        tmp_path / "capsules",
        mirror_writer=lambda rel_path, payload: mirrored.__setitem__(rel_path, payload),
    )
    cap = store.save_capsule(
        "bob",
        epoch=3,
        classification="INTERNAL",
        last_scorecard_date="2026-10-28",
        open_items=[{"id": "rec-1", "title": "Trim merge queue batch size", "url": "https://example.invalid/1"}],
        cursors={"gh_events": "evt_100"},
        next_slots=["2026-10-28T17:00:00-07:00", "2026-10-29T10:00:00-07:00"],
        now=1700000000.0,
    )
    assert cap["role"] == "bob" and cap["epoch"] == 3
    assert cap["last_scorecard_date"] == "2026-10-28"
    assert "capsules/bob.json" in mirrored
    assert mirrored["capsules/bob.json"]["projection"] == "full"

    # Stale epoch on save_capsule is rejected
    with pytest.raises(CapsuleStaleEpochError):
        store.save_capsule("bob", epoch=2, now=1700000010.0)


def test_fallback_ingest_accepts_epoch_plus_one_and_rejects_stale(tmp_path: Path) -> None:
    store = RoleCapsuleStore(tmp_path / "capsules")
    store.save_capsule("bob", epoch=5, last_scorecard_date="2026-10-28", next_slots=["slot-1"], now=1000.0)

    # Stale or equal epoch from fallback is rejected, leaving canonical state untouched
    with pytest.raises(CapsuleStaleEpochError):
        store.ingest_fallback_capsule(
            "bob",
            {"role": "bob", "epoch": 5, "open_items": [{"id": "stale", "title": "ignore"}], "next_slots": []},
            now=1050.0,
        )
    assert store.load_capsule("bob")["epoch"] == 5

    # Fallback writes epoch + 1 -> ingested on resume
    ingested = store.ingest_fallback_capsule(
        "bob",
        {
            "role": "bob",
            "epoch": 6,
            "last_scorecard_date": "2026-10-28",
            "open_items": [{"id": "rec-2", "title": "Fallback daily note", "url": "https://example.invalid/2"}],
            "cursors": {"daily_note": "2026-10-28"},
            "next_slots": ["2026-10-29T10:00:00-07:00"],
        },
        now=1100.0,
    )
    assert ingested["epoch"] == 6 and ingested["source"] == "grok-fallback"
    loaded = store.load_capsule("bob")
    assert loaded is not None and loaded["epoch"] == 6 and loaded["open_items"][0]["id"] == "rec-2"


def test_sensitive_role_emits_metadata_only_projection_to_mirror(tmp_path: Path) -> None:
    mirrored: dict[str, dict[str, object]] = {}
    store = RoleCapsuleStore(
        tmp_path / "capsules",
        mirror_writer=lambda rel_path, payload: mirrored.__setitem__(rel_path, payload),
    )
    cap = store.save_capsule(
        "smithers",
        epoch=2,
        classification="SENSITIVE",
        open_items=[{"id": "sec-1", "title": "Private internal audit detail", "url": "https://internal.invalid/a"}],
        cursors={"secret_stream": "cursor_private_value"},
        next_slots=["2026-10-29T08:00:00-07:00"],
        now=2000.0,
    )
    # Canonical local RD capsule retains full structured items, but mirror gets metadata-only emit_shared()
    assert len(cap["open_items"]) == 1
    shared = emit_shared(cap)
    assert shared["projection"] == "metadata_only"
    assert shared["open_item_count"] == 1
    assert shared["cursor_keys"] == ["secret_stream"]
    assert "open_items" not in shared and "cursors" not in shared
    assert "Private internal audit detail" not in str(shared)
    assert mirrored["capsules/smithers.json"] == shared


def test_dbc_enforces_8kb_cap_secret_redaction_and_valid_date(tmp_path: Path) -> None:
    store = RoleCapsuleStore(tmp_path / "capsules")
    huge_items = [{"id": f"i-{i}", "title": "x" * 200} for i in range(60)]
    with pytest.raises(CapsuleError, match="8 KB"):
        store.save_capsule("bob", epoch=1, open_items=huge_items, now=100.0)
    assert MAX_CAPSULE_BYTES == 8192

    # Secret or confidential marker is refused fail-closed
    fake_tok = "ghp_" + ("a" * 36)
    with pytest.raises(CapsuleError, match="secret|confidential"):
        store.save_capsule(
            "bob",
            epoch=1,
            open_items=[{"id": "1", "title": fake_tok}],
            now=100.0,
        )

    with pytest.raises(ValueError, match="last_scorecard_date"):
        store.save_capsule("bob", epoch=1, last_scorecard_date="not-a-date", now=100.0)
