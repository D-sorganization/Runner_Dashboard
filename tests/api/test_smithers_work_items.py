"""Tests for scoped agent-smithers principal and owner-scoped work items."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_agent_smithers_principal_is_codex_and_narrowly_scoped() -> None:
    """agent-smithers must use codex kind and narrow scopes without bot/barb/admin."""
    cfg = yaml.safe_load((REPO_ROOT / "config" / "agent_principals.yml").read_text(encoding="utf-8"))
    principals = {p["id"]: p for p in cfg["agents"]}
    sm = principals["agent-smithers"]
    assert sm["kind"] == "codex"
    assert set(sm["scopes"]) == {"staff.read", "staff.work_items.write"}
    assert "roles" not in sm


def test_work_item_idempotency_and_owner_scope_enforcement(tmp_path: Path) -> None:
    """idempotency_key deduplicates creates; scoped owners cannot read/mutate others' items."""
    from fastapi import HTTPException
    from identity import Principal
    from routers.staff_work_items import (
        CreateWorkItemRequest,
        PatchWorkItemRequest,
        create_work_item,
        get_work_item,
        list_work_items,
        patch_work_item,
    )
    from staff import work_items as wi_mod

    wi_mod.reset_work_item_store()
    wi_mod.get_work_item_store(db_path=tmp_path / "work_items.sqlite3")
    try:
        smithers = Principal(
            id="agent-smithers",
            type="agent",
            name="Smithers",
            scopes=["staff.read", "staff.work_items.write"],
        )
        other = Principal(
            id="agent-other",
            type="agent",
            name="Other Agent",
            scopes=["staff.read", "staff.work_items.write"],
        )

        body = CreateWorkItemRequest(
            title="Generate quarterly technical report",
            owner_role="smithers",
            idempotency_key="sm-req-001",
            output_format="pdf",
            artifact_refs=["report://draft-1"],
        )
        c1 = asyncio.run(create_work_item(body, caller=smithers))
        c2 = asyncio.run(create_work_item(body, caller=smithers))
        assert c1["id"] == c2["id"]
        assert c1["links"]["output_format"] == ["pdf"]
        assert c1["links"]["artifact_refs"] == ["report://draft-1"]

        # Same idempotency_key with changed payload returns 409 conflict.
        with pytest.raises(HTTPException) as exc_idem:
            asyncio.run(
                create_work_item(
                    CreateWorkItemRequest(title="Different title", idempotency_key="sm-req-001"),
                    caller=smithers,
                )
            )
        assert exc_idem.value.status_code == 409

        # Owner can read, list, and transition own item.
        got = asyncio.run(get_work_item(c1["id"], caller=smithers))
        assert got["id"] == c1["id"]

        listed_other = asyncio.run(
            list_work_items(
                thread_id=None,
                state=None,
                owner_role=None,
                requested_by=None,
                mine=None,
                waiting_on_me=None,
                overdue=None,
                limit=50,
                cursor=None,
                caller=other,
            )
        )
        assert listed_other["items"] == []

        patched = asyncio.run(
            patch_work_item(
                c1["id"],
                PatchWorkItemRequest(state="in_progress", artifact_refs=["report://final.pdf"]),
                caller=smithers,
            )
        )
        assert patched["state"] == "in_progress"
        assert patched["links"]["artifact_refs"] == ["report://final.pdf"]

        # Unsafe path traversal in artifact_refs fails validation before state change.
        with pytest.raises(HTTPException) as exc_bad:
            asyncio.run(
                patch_work_item(
                    c1["id"],
                    PatchWorkItemRequest(state="done", artifact_refs=["../secret.txt"]),
                    caller=smithers,
                )
            )
        assert exc_bad.value.status_code == 422
        assert asyncio.run(get_work_item(c1["id"], caller=smithers))["state"] == "in_progress"

        # Another scoped caller cannot read or patch Smithers' work item.
        with pytest.raises(HTTPException) as exc_get:
            asyncio.run(get_work_item(c1["id"], caller=other))
        assert exc_get.value.status_code == 403

        with pytest.raises(HTTPException) as exc_patch:
            asyncio.run(
                patch_work_item(
                    c1["id"],
                    PatchWorkItemRequest(state="cancelled"),
                    caller=other,
                )
            )
        assert exc_patch.value.status_code == 403
    finally:
        wi_mod.reset_work_item_store()
