"""Fleet context injection for staff chat turns (#1627).

Builds the ``## Fleet now`` block injected into chat prompts for roles declaring
target-free read tools: ``read_staff_summary``, ``read_priorities``, ``read_briefing``,
and ``read_sessions``.

Targeted read tools (``read_run``, ``read_issue``, ``read_repo``) require a specific
target identifier and are NOT handled here.

Each source runs on its own private event loop in a worker thread (#1761): a
source performing synchronous blocking work (sync sqlite/file/subprocess calls
inside an ``async def``) freezes only that thread, not the shared loop driving
the chat turn and the other concurrently gathered sources, so one slow or
blocking source cannot sweep its siblings into the same timeout. On timeout or
error, a tool that has succeeded earlier this process renders its last-good
payload as ``stale (age Ns): <body>`` instead of ``unavailable``; a tool with
no prior success still renders ``unavailable``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from staff.roles import RoleSpec

__all__ = [
    "FLEET_CONTEXT_TOOLS",
    "MAX_BLOCK_CHARS",
    "MAX_SECTION_CHARS",
    "SOURCE_TIMEOUT_SECONDS",
    "build_fleet_context_block",
    "declared_fleet_tools",
]

log = logging.getLogger("dashboard.staff.chat_fleet_context")

FLEET_CONTEXT_TOOLS: tuple[str, ...] = (
    "read_staff_summary",
    "read_priorities",
    "read_briefing",
    "read_sessions",
)

SOURCE_TIMEOUT_SECONDS: float = 5.0
MAX_SECTION_CHARS: int = 2500
MAX_BLOCK_CHARS: int = 8000
TRUNCATION_MARKER: str = " …(truncated)"

HEADER_TEMPLATE: str = (
    "Read by the dashboard at {time_str} for this turn. These are the facts; "
    "you cannot run curl or any shell command in a chat turn, so do not ask for "
    "tool approval. A section marked 'stale (age Ns)' held from an earlier turn "
    "and may be that many seconds out of date; say so rather than presenting it "
    "as current. If a section says unavailable, say so rather than guessing."
)

# Per-tool last-good snapshot: tool -> (fetched_at, rendered_body). Populated on
# every successful fetch and served (as "stale (age Ns): <body>") when a later
# fetch times out or errors, instead of "unavailable" (#1761). Module-level and
# unlocked: mutated only from coroutines running on the caller's event loop, which
# is single-threaded, so concurrent `_run_source` calls never race on it.
_LAST_GOOD: dict[str, tuple[datetime, str]] = {}


async def _fetch_staff_summary() -> dict[str, Any]:
    """Fetch staff summary via lazy import to avoid circular dependencies."""
    from staff.summary_view import build_staff_summary  # noqa: PLC0415

    return await build_staff_summary()


async def _fetch_priorities() -> dict[str, Any]:
    """Fetch priorities snapshot via lazy import wrapped in thread."""
    from priorities.service import priorities_snapshot  # noqa: PLC0415

    return await asyncio.to_thread(priorities_snapshot)


async def _fetch_briefing() -> dict[str, Any]:
    """Fetch pre-work briefing via lazy import."""
    from coordination.briefing import build_briefing  # noqa: PLC0415

    return await build_briefing(None, None)


async def _fetch_sessions() -> dict[str, Any]:
    """Fetch coordination sessions via lazy import."""
    from coordination.service import sessions  # noqa: PLC0415

    return await sessions(None)


DEFAULT_SOURCES: Mapping[str, Callable[[], Awaitable[dict[str, Any]]]] = {
    "read_staff_summary": _fetch_staff_summary,
    "read_priorities": _fetch_priorities,
    "read_briefing": _fetch_briefing,
    "read_sessions": _fetch_sessions,
}


def declared_fleet_tools(role: RoleSpec | None) -> tuple[str, ...]:
    """Return declared members of FLEET_CONTEXT_TOOLS in FLEET_CONTEXT_TOOLS order.

    Precondition: role is optional.
    Postcondition: returns a tuple of tool names in FLEET_CONTEXT_TOOLS order,
    or () if role is None or declares none of the fleet context tools.
    """
    if role is None or not isinstance(role.chat, dict):
        return ()
    raw_tools = role.chat.get("tools")
    if not isinstance(raw_tools, (list, tuple, set)):
        return ()
    tool_set = set(raw_tools)
    return tuple(tool for tool in FLEET_CONTEXT_TOOLS if tool in tool_set)


async def _await_source(source_fn: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Await source_fn() as a plain coroutine, regardless of its concrete awaitable type."""
    return await source_fn()


def _run_source_isolated(source_fn: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Run one source's coroutine to completion on a fresh event loop.

    Precondition: source_fn takes no arguments and returns an awaitable.
    Postcondition: returns the awaited payload, or propagates source_fn's exception.
    Called via ``asyncio.to_thread`` so this executes in its own worker thread with
    its own event loop, isolating any synchronous blocking work inside source_fn
    from the shared loop and from every other concurrently gathered source (#1761).
    """
    return asyncio.run(_await_source(source_fn))


async def _run_source(
    tool: str,
    source_fn: Callable[[], Awaitable[dict[str, Any]]] | None,
    now_fn: Callable[[], datetime],
) -> tuple[str, str]:
    """Execute a single source function under timeout and format as compact JSON.

    Precondition: tool is a non-empty string; now_fn returns a timezone-aware datetime.
    Postcondition: returns (tool, rendered_body). Never raises exceptions. On success,
    the rendered body is remembered as this tool's last-good snapshot. On timeout or
    error, renders that snapshot as "stale (age Ns): <body>" when one exists, else
    "unavailable (ExceptionName)".
    """
    try:
        if source_fn is None:
            raise KeyError(tool)
        payload = await asyncio.wait_for(
            asyncio.to_thread(_run_source_isolated, source_fn),
            timeout=SOURCE_TIMEOUT_SECONDS,
        )
        body = json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)
        if len(body) > MAX_SECTION_CHARS:
            body = body[:MAX_SECTION_CHARS] + TRUNCATION_MARKER
        _LAST_GOOD[tool] = (now_fn(), body)
    except Exception as exc:  # noqa: BLE001
        log.warning("Fleet context tool '%s' unavailable: %s", tool, type(exc).__name__)
        snapshot = _LAST_GOOD.get(tool)
        if snapshot is None:
            body = f"unavailable ({type(exc).__name__})"
        else:
            snapshot_time, snapshot_body = snapshot
            age_seconds = max(0, round((now_fn() - snapshot_time).total_seconds()))
            body = f"stale (age {age_seconds}s): {snapshot_body}"
    return tool, body


async def build_fleet_context_block(
    role: RoleSpec | None,
    sources: Mapping[str, Callable[[], Awaitable[dict[str, Any]]]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> str | None:
    """Build the '## Fleet now' markdown block for target-free read tools.

    Precondition: none beyond types.
    Postcondition: returns None when the role declares none of the tools;
    otherwise a markdown string starting with '## Fleet now' and at most
    MAX_BLOCK_CHARS long. Never raises (orthogonality).
    """
    tools = declared_fleet_tools(role)
    if not tools:
        return None

    active_sources = sources if sources is not None else DEFAULT_SOURCES
    now_fn = now if now is not None else lambda: datetime.now(UTC)

    try:
        dt = now_fn()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        time_str = dt.isoformat().replace("+00:00", "Z")
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed determining time for fleet context: %s", exc)
        time_str = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    tasks = [_run_source(tool, active_sources.get(tool), now_fn) for tool in tools]
    results = await asyncio.gather(*tasks)

    parts = [
        "## Fleet now",
        HEADER_TEMPLATE.format(time_str=time_str),
    ]
    for tool, body in results:
        parts.append(f"### {tool}\n{body}")

    block = "\n\n".join(parts).strip()
    if len(block) > MAX_BLOCK_CHARS:
        block = block[: MAX_BLOCK_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

    return block
