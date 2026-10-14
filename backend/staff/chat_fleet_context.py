"""Fleet context injection for staff chat turns (#1627, #1803).

Builds the ``## Fleet now`` block injected into chat prompts for roles declaring
target-free read tools: ``read_staff_summary``, ``read_priorities``, ``read_briefing``,
and ``read_sessions``.

Targeted read tools (``read_run``, ``read_repo``) are handled via typed, bounded
handlers exposing source node, captured-at time, SHA, scope, and truncation manifest
(BR-10, #1803).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from staff.roles import RoleSpec, load_roles

__all__ = [
    "ALL_FLEET_TOOLS",
    "COLD_SOURCE_TIMEOUT_SECONDS",
    "FLEET_CONTEXT_TOOLS",
    "MAX_BLOCK_CHARS",
    "MAX_SECTION_CHARS",
    "SOURCE_TIMEOUT_SECONDS",
    "TARGETED_FLEET_TOOLS",
    "build_fleet_context_block",
    "build_target_context_block",
    "declared_fleet_tools",
    "parse_repo_refs",
    "parse_run_refs",
    "read_repo_context",
    "read_run_context",
    "warm_fleet_context_snapshots",
]

log = logging.getLogger("dashboard.staff.chat_fleet_context")

FLEET_CONTEXT_TOOLS: tuple[str, ...] = (
    "read_staff_summary",
    "read_priorities",
    "read_briefing",
    "read_sessions",
)
TARGETED_FLEET_TOOLS: tuple[str, ...] = ("read_run", "read_repo")
ALL_FLEET_TOOLS: tuple[str, ...] = FLEET_CONTEXT_TOOLS + TARGETED_FLEET_TOOLS

DEFAULT_KNOWN_REPO_NAMES: tuple[str, ...] = (
    "Runner_Dashboard",
    "Repository_Management",
    "Maxwell_Daemon",
    "UpstreamDrift",
    "Tools_Private",
    "Tools",
)

_RUN_REF_RE = re.compile(r"\b(run-[A-Za-z0-9_.-]+)\b")
_RUN_URL_RE = re.compile(r"/runs/(run-[A-Za-z0-9_.-]+)\b")

SOURCE_TIMEOUT_SECONDS: float = 5.0
COLD_SOURCE_TIMEOUT_SECONDS: float = 15.0
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

_LAST_GOOD: dict[str, tuple[datetime, str]] = {}


def parse_run_refs(text: str) -> list[str]:
    """Recognise up to 3 run references in text."""
    if not text:
        return []
    refs: list[str] = []
    seen: set[str] = set()
    for m in _RUN_URL_RE.finditer(text):
        rid = m.group(1)
        if rid not in seen:
            seen.add(rid)
            refs.append(rid)
    for m in _RUN_REF_RE.finditer(text):
        rid = m.group(1)
        if rid not in seen:
            seen.add(rid)
            refs.append(rid)
    return refs[:3]


def parse_repo_refs(text: str, repo_names: tuple[str, ...] = ()) -> list[str]:
    """Recognise up to 3 known repository references in text."""
    if not text:
        return []
    names = repo_names or DEFAULT_KNOWN_REPO_NAMES
    canonical = {name.lower(): name for name in names}
    alternation = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    repo_re = re.compile(rf"\b({alternation})\b", re.IGNORECASE)
    refs: list[str] = []
    seen: set[str] = set()
    for m in repo_re.finditer(text):
        matched = canonical[m.group(1).lower()]
        if matched not in seen:
            seen.add(matched)
            refs.append(matched)
    return refs[:3]


def read_run_context(
    run_id: str,
    store: Any = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Retrieve structured run facts with machine-readable provenance (BR-10, #1803)."""
    now = now_fn() if now_fn is not None else datetime.now(UTC)
    time_iso = now.isoformat().replace("+00:00", "Z")
    node = socket.gethostname()

    target_store = store
    if target_store is None:
        try:
            from staff.runner import get_runner  # noqa: PLC0415

            target_store = get_runner().store
        except Exception:  # noqa: BLE001
            target_store = None

    if target_store is None or not hasattr(target_store, "get_run"):
        return {
            "tool": "read_run",
            "target": run_id,
            "status": "unavailable",
            "reason": "store_unavailable",
            "source_node": node,
            "captured_at": time_iso,
            "sha": None,
            "scope": "run",
            "truncation": {"truncated": False, "omitted_sections": []},
        }

    rec = target_store.get_run(run_id)
    if rec is None:
        return {
            "tool": "read_run",
            "target": run_id,
            "status": "unavailable",
            "reason": "not_found",
            "source_node": node,
            "captured_at": time_iso,
            "sha": None,
            "scope": "run",
            "truncation": {"truncated": False, "omitted_sections": []},
        }

    sha = getattr(rec, "head_ref", None) or getattr(rec, "target_ref", None) or ""
    return {
        "tool": "read_run",
        "target": run_id,
        "status": getattr(rec, "status", "unknown"),
        "role": getattr(rec, "role", ""),
        "repo": getattr(rec, "repo", ""),
        "source_node": getattr(rec, "machine", "") or node,
        "captured_at": time_iso,
        "sha": sha,
        "scope": "run",
        "started_at": getattr(rec, "started_at", None),
        "ended_at": getattr(rec, "ended_at", None),
        "error": getattr(rec, "error", None) or None,
        "summary": getattr(rec, "summary", "") or None,
        "truncation": {"truncated": False, "omitted_sections": []},
    }


def read_repo_context(
    repo_name: str,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Retrieve structured repository facts with machine-readable provenance (BR-10, #1803)."""
    now = now_fn() if now_fn is not None else datetime.now(UTC)
    time_iso = now.isoformat().replace("+00:00", "Z")
    node = socket.gethostname()

    if not repo_name or (repo_name not in DEFAULT_KNOWN_REPO_NAMES):
        return {
            "tool": "read_repo",
            "target": repo_name,
            "status": "unavailable",
            "reason": "unknown_repo",
            "source_node": node,
            "captured_at": time_iso,
            "sha": None,
            "scope": "repo",
            "truncation": {"truncated": False, "omitted_sections": []},
        }

    sha = "HEAD"
    try:
        from staff import workspace  # noqa: PLC0415

        checkout = workspace.find_repo_checkout(repo_name)
        if checkout and hasattr(workspace, "rev_parse_head"):
            sha = workspace.rev_parse_head(checkout) or "HEAD"
    except Exception:  # noqa: BLE001
        sha = "HEAD"

    return {
        "tool": "read_repo",
        "target": repo_name,
        "status": "available",
        "source_node": node,
        "captured_at": time_iso,
        "sha": sha,
        "scope": "repo",
        "truncation": {"truncated": False, "omitted_sections": []},
    }


def build_target_context_block(
    role: RoleSpec | None,
    text: str,
    store: Any = None,
    now: Callable[[], datetime] | None = None,
) -> str | None:
    """Build markdown block for declared targeted tools (read_run, read_repo)."""
    if role is None or not isinstance(role.chat, dict):
        return None
    tools = set(role.chat.get("tools") or ())
    has_read_run = "read_run" in tools
    has_read_repo = "read_repo" in tools
    if not (has_read_run or has_read_repo):
        return None

    now_fn = now if now is not None else lambda: datetime.now(UTC)
    sections: list[str] = []

    if has_read_run:
        runs = parse_run_refs(text)
        for r_id in runs:
            payload = read_run_context(r_id, store=store, now_fn=now_fn)
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            sections.append(f"### read_run: {r_id}\n{body}")

    if has_read_repo:
        repos = parse_repo_refs(text)
        for r_name in repos:
            payload = read_repo_context(r_name, now_fn=now_fn)
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            sections.append(f"### read_repo: {r_name}\n{body}")

    if not sections:
        return None

    header = (
        "## Targeted context\n"
        "Read by the dashboard for this turn with machine-readable provenance. "
        "A section marked unavailable could not be verified."
    )
    return header + "\n\n" + "\n\n".join(sections)


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
    """Return declared members of FLEET_CONTEXT_TOOLS in FLEET_CONTEXT_TOOLS order."""
    if role is None or not isinstance(role.chat, dict):
        return ()
    raw_tools = role.chat.get("tools")
    if not isinstance(raw_tools, (list, tuple, set)):
        return ()
    tool_set = set(raw_tools)
    return tuple(tool for tool in FLEET_CONTEXT_TOOLS if tool in tool_set)


async def _warm_role_cache() -> None:
    """Refresh the ``staff.roles.load_roles`` mtime-cache off the event loop."""
    try:
        await asyncio.to_thread(load_roles)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed warming staff role cache before fleet context: %s", exc)


async def _run_source(
    tool: str,
    source_fn: Callable[[], Awaitable[dict[str, Any]]] | None,
    now_fn: Callable[[], datetime],
) -> tuple[str, str]:
    """Execute a single source function under timeout and format as compact JSON."""
    try:
        if source_fn is None:
            raise KeyError(tool)
        timeout = COLD_SOURCE_TIMEOUT_SECONDS if tool not in _LAST_GOOD else SOURCE_TIMEOUT_SECONDS
        payload = await asyncio.wait_for(source_fn(), timeout=timeout)
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


async def warm_fleet_context_snapshots(
    sources: Mapping[str, Callable[[], Awaitable[dict[str, Any]]]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> None:
    """Pre-warm last-good snapshots for all fleet context tools."""
    active_sources = sources if sources is not None else DEFAULT_SOURCES
    now_fn = now if now is not None else lambda: datetime.now(UTC)
    await _warm_role_cache()
    tasks = [_run_source(tool, active_sources.get(tool), now_fn) for tool in FLEET_CONTEXT_TOOLS]
    await asyncio.gather(*tasks, return_exceptions=True)


async def build_fleet_context_block(
    role: RoleSpec | None,
    sources: Mapping[str, Callable[[], Awaitable[dict[str, Any]]]] | None = None,
    now: Callable[[], datetime] | None = None,
    raw_prompt: str = "",
    store: Any = None,
) -> str | None:
    """Build the '## Fleet now' markdown block for target-free read tools.

    When raw_prompt is supplied and role declares targeted tools (read_run, read_repo),
    targeted context is gathered and appended. Truncation includes section manifest (BR-10).
    """
    tools = declared_fleet_tools(role)
    target_block = build_target_context_block(role, raw_prompt, store=store, now=now) if raw_prompt else None
    if not tools and not target_block:
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

    await _warm_role_cache()

    parts: list[str] = []
    if tools:
        tasks = [_run_source(tool, active_sources.get(tool), now_fn) for tool in tools]
        results = await asyncio.gather(*tasks)

        parts.append("## Fleet now")
        parts.append(HEADER_TEMPLATE.format(time_str=time_str))
        for tool, body in results:
            parts.append(f"### {tool}\n{body}")

    if target_block:
        parts.append(target_block)

    block = "\n\n".join(parts).strip()
    if len(block) > MAX_BLOCK_CHARS:
        block = block[: MAX_BLOCK_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

    return block
