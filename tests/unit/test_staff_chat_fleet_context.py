"""Unit tests for fleet context injection in staff chat turns (Issue Barb fleet context)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import staff.chat_fleet_context as cfc
from staff.chat_fleet_context import (
    FLEET_CONTEXT_TOOLS,
    MAX_BLOCK_CHARS,
    MAX_SECTION_CHARS,
    build_fleet_context_block,
    declared_fleet_tools,
)
from staff.roles import RoleSpec


@pytest.mark.unit
def test_declared_fleet_tools_filtering_and_order() -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        chat={"tools": ["read_priorities", "read_staff_summary", "read_run", "search_knowledge"]},
    )
    assert declared_fleet_tools(role) == ("read_staff_summary", "read_priorities")
    assert declared_fleet_tools(None) == ()

    role_no_tools = RoleSpec(name="barb", title="Barb", chat={})
    assert declared_fleet_tools(role_no_tools) == ()

    role_empty_tools = RoleSpec(name="barb", title="Barb", chat={"tools": []})
    assert declared_fleet_tools(role_empty_tools) == ()

    role_non_list_tools = RoleSpec(name="barb", title="Barb", chat={"tools": "invalid"})  # type: ignore[arg-type]
    assert declared_fleet_tools(role_non_list_tools) == ()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_returns_none_when_no_tools() -> None:
    role = RoleSpec(
        name="disciple",
        title="Disciple",
        chat={"tools": ["search_knowledge"]},
    )
    fake_sources = {
        "read_staff_summary": AsyncMock(return_value={"hub": "Desk"}),
    }
    block = await build_fleet_context_block(role, sources=fake_sources)
    assert block is None
    fake_sources["read_staff_summary"].assert_not_called()

    block_none = await build_fleet_context_block(None, sources=fake_sources)
    assert block_none is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_renders_sections_and_json() -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        chat={"tools": ["read_priorities", "read_staff_summary"]},
    )
    fake_sources = {
        "read_staff_summary": AsyncMock(return_value={"hub": "Desk", "online": ["Desk"]}),
        "read_priorities": AsyncMock(return_value={"available": True, "directives": ["focus"]}),
    }
    fixed_time = datetime(2026, 9, 27, 3, 51, 11, tzinfo=UTC)
    block = await build_fleet_context_block(role, sources=fake_sources, now=lambda: fixed_time)

    assert block is not None
    assert block.startswith("## Fleet now")
    assert "Read by the dashboard at 2026-09-27T03:51:11Z for this turn." in block
    assert "you cannot run curl" in block

    # Verify section ordering: read_staff_summary before read_priorities
    summary_idx = block.index("### read_staff_summary")
    priorities_idx = block.index("### read_priorities")
    assert summary_idx < priorities_idx

    assert '{"hub":"Desk","online":["Desk"]}' in block
    assert '{"available":true,"directives":["focus"]}' in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_redacts_exception_message() -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        chat={"tools": ["read_staff_summary"]},
    )

    async def _failing_source() -> dict[str, Any]:
        raise RuntimeError("secret /home/x path")

    fake_sources = {"read_staff_summary": _failing_source}
    block = await build_fleet_context_block(role, sources=fake_sources)

    assert block is not None
    assert "### read_staff_summary" in block
    assert "unavailable (RuntimeError)" in block
    assert "secret /home/x path" not in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_timeout_renders_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfc, "SOURCE_TIMEOUT_SECONDS", 0.05)

    role = RoleSpec(
        name="barb",
        title="Barb",
        chat={"tools": ["read_sessions"]},
    )

    async def _slow_source() -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"sessions": []}

    fake_sources = {"read_sessions": _slow_source}
    block = await build_fleet_context_block(role, sources=fake_sources)

    assert block is not None
    assert "### read_sessions" in block
    assert "unavailable (TimeoutError)" in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_truncates_large_payload_and_caps_total() -> None:
    role = RoleSpec(
        name="barb",
        title="Barb",
        chat={"tools": list(FLEET_CONTEXT_TOOLS)},
    )

    large_payload = {"data": "X" * (MAX_SECTION_CHARS + 500)}
    fake_sources = {tool: AsyncMock(return_value=large_payload) for tool in FLEET_CONTEXT_TOOLS}
    block = await build_fleet_context_block(role, sources=fake_sources)

    assert block is not None
    assert "…(truncated)" in block
    assert len(block) <= MAX_BLOCK_CHARS
    assert block.endswith(" …(truncated)")
