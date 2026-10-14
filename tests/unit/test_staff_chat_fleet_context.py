"""Unit tests for fleet context injection in staff chat turns (Issue Barb fleet context)."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
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


@pytest.fixture(autouse=True)
def _clear_last_good_snapshot_cache() -> Any:
    """Isolate the module-level last-good snapshot cache between tests (#1761)."""
    cfc._LAST_GOOD.clear()
    yield
    cfc._LAST_GOOD.clear()


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
    monkeypatch.setattr(cfc, "COLD_SOURCE_TIMEOUT_SECONDS", 0.05)

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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_warms_role_cache_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The role YAML cache is refreshed via asyncio.to_thread, not on the caller's loop (#1761).

    ``build_staff_summary`` (behind ``read_staff_summary``) calls
    ``staff.roles.load_roles()`` synchronously; on a cold mtime-cache — most likely
    shortly after a restart — that means a directory glob, a stat of every role file
    and a full YAML parse, run directly on the event loop, which is what froze the
    loop long enough to starve every concurrently gathered source at once. Warming
    the cache off-loop first, before gathering, means that later in-loop call only
    re-stats already-cached files. This asserts the warm-up call itself actually runs
    on a worker thread, not the thread driving this test's event loop.
    """
    main_thread = threading.current_thread()
    seen_threads: list[threading.Thread] = []

    def _fake_load_roles() -> dict[str, Any]:
        seen_threads.append(threading.current_thread())
        return {}

    monkeypatch.setattr(cfc, "load_roles", _fake_load_roles)

    role = RoleSpec(name="barb", title="Barb", chat={"tools": ["read_priorities"]})
    fake_sources = {"read_priorities": AsyncMock(return_value={"ok": True})}

    block = await build_fleet_context_block(role, sources=fake_sources)

    assert block is not None
    assert len(seen_threads) == 1, "load_roles must be warmed exactly once per turn"
    assert seen_threads[0] is not main_thread, "the warm-up must not run on the caller's event-loop thread"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_survives_role_cache_warm_up_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role-cache warm-up failure is logged and swallowed; sources still run (#1761)."""

    def _broken_load_roles() -> dict[str, Any]:
        raise OSError("role directory unreadable")

    monkeypatch.setattr(cfc, "load_roles", _broken_load_roles)

    role = RoleSpec(name="barb", title="Barb", chat={"tools": ["read_priorities"]})
    fake_sources = {"read_priorities": AsyncMock(return_value={"ok": True})}

    block = await build_fleet_context_block(role, sources=fake_sources)

    assert block is not None
    assert '{"ok":true}' in block


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_serves_stale_snapshot_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool that later times out renders its last-good payload as stale, not unavailable (#1761)."""
    monkeypatch.setattr(cfc, "SOURCE_TIMEOUT_SECONDS", 0.05)

    role = RoleSpec(name="barb", title="Barb", chat={"tools": ["read_sessions"]})
    good_payload = {"sessions": ["a"]}
    calls = {"n": 0}

    async def _flaky() -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return good_payload
        await asyncio.sleep(0.3)
        return {"sessions": ["should not appear"]}

    fake_sources = {"read_sessions": _flaky}

    t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
    block1 = await build_fleet_context_block(role, sources=fake_sources, now=lambda: t0)
    assert block1 is not None
    assert '{"sessions":["a"]}' in block1

    t1 = t0 + timedelta(seconds=42)
    block2 = await build_fleet_context_block(role, sources=fake_sources, now=lambda: t1)
    assert block2 is not None
    section = block2[block2.index("### read_sessions") :]
    assert section.startswith('### read_sessions\nstale (age 42s): {"sessions":["a"]}')
    assert "unavailable" not in section


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_fleet_context_block_no_snapshot_yet_still_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool with no prior success this process still renders unavailable, not a fabricated stale body."""
    monkeypatch.setattr(cfc, "SOURCE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cfc, "COLD_SOURCE_TIMEOUT_SECONDS", 0.05)

    role = RoleSpec(name="barb", title="Barb", chat={"tools": ["read_briefing"]})

    async def _slow_source() -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"briefing": []}

    block = await build_fleet_context_block(role, sources={"read_briefing": _slow_source})
    assert block is not None
    section = block[block.index("### read_briefing") :]
    assert section == "### read_briefing\nunavailable (TimeoutError)"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cold_first_turn_slower_than_steady_timeout_succeeds_under_cold_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold first turn slower than steady-state SOURCE_TIMEOUT_SECONDS succeeds
    within COLD_SOURCE_TIMEOUT_SECONDS (#1768).
    """
    monkeypatch.setattr(cfc, "SOURCE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cfc, "COLD_SOURCE_TIMEOUT_SECONDS", 0.3)

    role = RoleSpec(name="barb", title="Barb", chat={"tools": ["read_briefing"]})

    async def _cold_source() -> dict[str, Any]:
        # Slower than steady-state 0.05s, but within cold budget 0.3s
        await asyncio.sleep(0.12)
        return {"briefing": ["node-1", "node-2"]}

    block = await build_fleet_context_block(role, sources={"read_briefing": _cold_source})
    assert block is not None
    section = block[block.index("### read_briefing") :]
    assert "unavailable" not in section
    assert '{"briefing":["node-1","node-2"]}' in section


@pytest.mark.unit
@pytest.mark.asyncio
async def test_warm_fleet_context_snapshots_populates_last_good(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-warming populates last-good snapshots so subsequent timeouts render stale (#1768)."""
    fake_sources = {
        "read_staff_summary": AsyncMock(return_value={"summary": "warm"}),
        "read_priorities": AsyncMock(return_value={"priorities": "warm"}),
        "read_briefing": AsyncMock(return_value={"briefing": "warm"}),
        "read_sessions": AsyncMock(return_value={"sessions": "warm"}),
    }

    assert len(cfc._LAST_GOOD) == 0
    await cfc.warm_fleet_context_snapshots(sources=fake_sources)
    assert len(cfc._LAST_GOOD) == 4
    for tool in cfc.FLEET_CONTEXT_TOOLS:
        assert tool in cfc._LAST_GOOD
        assert '"warm"' in cfc._LAST_GOOD[tool][1]


@pytest.mark.unit
def test_read_run_context_unindexed_returns_structured_unavailable() -> None:
    """Querying an unindexed run returns typed unavailability metadata (BR-10)."""
    fixed_time = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    class FakeStore:
        def get_run(self, rid: str) -> Any:
            return None

    res = cfc.read_run_context("run-missing-999", store=FakeStore(), now_fn=lambda: fixed_time)
    assert res["status"] == "unavailable"
    assert res["reason"] == "not_found"
    assert res["target"] == "run-missing-999"
    assert res["captured_at"] == "2026-10-14T12:00:00Z"
    assert res["sha"] is None
    assert res["scope"] == "run"
    assert res["truncation"] == {"truncated": False, "omitted_sections": []}
    assert bool(res["source_node"])


@pytest.mark.unit
def test_read_run_context_known_run_returns_typed_facts() -> None:
    """Querying an existing run returns complete run facts with provenance (BR-10)."""
    fixed_time = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    class FakeRun:
        status = "succeeded"
        role = "barb"
        repo = "Runner_Dashboard"
        machine = "desk-node-1"
        started_at = "2026-10-14T11:00:00Z"
        ended_at = "2026-10-14T11:05:00Z"
        error = None
        summary = "Completed task"
        head_ref = "feat/test-branch"

    class FakeStore:
        def get_run(self, rid: str) -> Any:
            return FakeRun()

    res = cfc.read_run_context("run-ok-123", store=FakeStore(), now_fn=lambda: fixed_time)
    assert res["status"] == "succeeded"
    assert res["role"] == "barb"
    assert res["repo"] == "Runner_Dashboard"
    assert res["source_node"] == "desk-node-1"
    assert res["sha"] == "feat/test-branch"
    assert res["captured_at"] == "2026-10-14T12:00:00Z"
    assert res["summary"] == "Completed task"


@pytest.mark.unit
def test_read_repo_context_known_and_unknown() -> None:
    """Querying repo facts distinguishes known from unknown targets (BR-10)."""
    fixed_time = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    unknown = cfc.read_repo_context("NonexistentRepo", now_fn=lambda: fixed_time)
    assert unknown["status"] == "unavailable"
    assert unknown["reason"] == "unknown_repo"
    assert unknown["scope"] == "repo"

    known = cfc.read_repo_context("Runner_Dashboard", now_fn=lambda: fixed_time)
    assert known["status"] == "available"
    assert known["scope"] == "repo"
    assert known["captured_at"] == "2026-10-14T12:00:00Z"
    assert bool(known["sha"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_target_context_block_in_fleet_context() -> None:
    """Targeted read tools inject machine-readable blocks when referenced (BR-10)."""
    role = RoleSpec(
        name="barb",
        title="Barb",
        chat={"tools": ["read_priorities", "read_run", "read_repo"]},
    )

    class FakeRun:
        status = "failed"
        role = "barb"
        repo = "UpstreamDrift"
        machine = "desk-1"
        started_at = "2026-10-14T10:00:00Z"
        ended_at = "2026-10-14T10:02:00Z"
        error = "timeout"
        summary = ""
        head_ref = "abc1234"

    class FakeStore:
        def get_run(self, rid: str) -> Any:
            return FakeRun() if rid == "run-failing-101" else None

    fake_sources = {
        "read_priorities": AsyncMock(return_value={"directives": []}),
    }
    prompt = "Can you inspect run-failing-101 on UpstreamDrift?"
    block = await build_fleet_context_block(role, sources=fake_sources, raw_prompt=prompt, store=FakeStore())

    assert block is not None
    assert "## Targeted context" in block
    assert "### read_run: run-failing-101" in block
    assert "### read_repo: UpstreamDrift" in block
    assert '"timeout"' in block


@pytest.mark.unit
def test_unsupported_role_tools_fail_validation() -> None:
    """Declaring unsupported chat tools fails schema validation (BR-10)."""
    from staff.validator import validate_role_data

    valid_role_data = {
        "name": "test-bot",
        "title": "Test Bot",
        "summary": "Valid summary",
        "playbook": "docs/test.md",
        "prompt_template": None,
        "instructions": "Do work",
        "providers": ["claude"],
        "model": "default",
        "schedule": None,
        "window": None,
        "repos": ["Runner_Dashboard"],
        "scope": {},
        "budget": {"usd_per_run": 1.0, "usd_per_day": 10.0},
        "permissions": {
            "lease": True,
            "push_branch": True,
            "open_pr": True,
            "merge": False,
            "host_shell": False,
            "notify_user": False,
        },
        "reports_to": "user",
        "holds": [],
        "surface": "dashboard",
        "chat": {"tools": ["read_run", "read_repo", "read_issue"]},
    }
    assert validate_role_data(valid_role_data) == []

    invalid_role_data = dict(valid_role_data)
    invalid_role_data["chat"] = {"tools": ["read_run", "execute_arbitrary_shell"]}
    errs = validate_role_data(invalid_role_data)
    assert any("unsupported tool" in e for e in errs)
