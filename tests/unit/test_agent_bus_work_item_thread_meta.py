"""Work-item thread meta is forward-compatible with slice 3b handoff documents."""

from __future__ import annotations

import pytest

from agent_bus.work_item_thread import (
    HANDOFF_DOCUMENT_SCHEMA_VERSION,
    ensure_handoff_document_slot,
    new_work_item_thread_meta,
)


@pytest.mark.unit
def test_new_work_item_meta_reserves_handoff_document_slot() -> None:
    meta = new_work_item_thread_meta("AffineDrift", 5117, "issue")
    assert meta["handoff_document"] is None
    assert meta["agent_bus_work_item"] is True


@pytest.mark.unit
def test_ensure_slot_upgrades_legacy_slice1_meta_without_rewrite() -> None:
    legacy = {"agent_bus_work_item": True, "repo": "Tools", "number": 1, "work_kind": "pr"}
    upgraded = ensure_handoff_document_slot(legacy)
    assert upgraded["handoff_document"] is None
    assert upgraded["repo"] == "Tools"


@pytest.mark.unit
def test_slice3b_document_shape_is_versioned_json_in_meta() -> None:
    """Slice 3b stores the document in thread meta; envelope version is explicit."""
    meta = new_work_item_thread_meta("Runner_Dashboard", 2155, "issue")
    meta["handoff_document"] = {
        "schema_version": HANDOFF_DOCUMENT_SCHEMA_VERSION,
        "revision": 1,
        "updated_at": "2026-10-22T04:00:00Z",
        "body": {"summary": "hand off to issue-remediator", "from_role": "pr-remediator"},
    }
    assert meta["handoff_document"]["schema_version"] == 1
