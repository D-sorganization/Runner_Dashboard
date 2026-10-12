"""The 500-line-cap split keeps the original import surface (#1800)."""

from __future__ import annotations

from pathlib import Path

from staff import barb_store, conversations, run_schema, store


def test_conversation_store_inherits_barb_methods() -> None:
    assert issubclass(conversations.ConversationStore, barb_store.BarbAuthorityMixin)
    assert conversations.ConversationStore.apply_run_card_update is barb_store.BarbAuthorityMixin.apply_run_card_update
    assert conversations.ConversationStore.barb_outbox is barb_store.BarbAuthorityMixin.barb_outbox


def test_store_still_uses_the_added_columns_from_run_schema() -> None:
    assert store._ADDED_COLUMNS is run_schema._ADDED_COLUMNS
    assert ("card_version", "INTEGER NOT NULL DEFAULT 0") in run_schema._ADDED_COLUMNS


def test_split_modules_stay_under_the_line_cap() -> None:
    for mod in (conversations, store, barb_store, run_schema):
        assert len(Path(mod.__file__).read_text(encoding="utf-8").splitlines()) <= 500
