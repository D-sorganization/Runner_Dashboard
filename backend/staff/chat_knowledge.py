"""Knowledge-pack retrieval and citation formatting for staff chat turns (Issue #1479).

Builds the retrieval-augmented ``## Knowledge`` block injected into chat prompts
for roles whose `search_knowledge` tool is enabled. Split out of `staff.chat`
to keep that module under the repo's 500-line soft cap.
"""

from __future__ import annotations

import logging
from pathlib import Path

from knowledge_pack import KnowledgePack
from staff.codebase_freshness import snapshot_is_stale
from staff.knowledge_refresh import get_knowledge_dir, pack_is_stale, resolve_pack_roots
from staff.roles import RoleSpec

__all__ = ["build_knowledge_turn_block"]

log = logging.getLogger("dashboard.staff.chat_knowledge")


def _tools(role: RoleSpec) -> set[str]:
    chat_tools = list(role.chat.get("tools") or []) if isinstance(role.chat, dict) else []
    return set(role.tools) | set(chat_tools)


def _pack_id(role: RoleSpec) -> str:
    name = (role.scope or {}).get("pack")
    return Path(name).stem if name else role.name


def build_knowledge_turn_block(role: RoleSpec | None, query: str) -> str | None:
    """Supply declared indexed-code and role knowledge tools without cold scans."""
    if role is None:
        return None
    tools = _tools(role)
    blocks: list[str | None] = []
    if "search_code" in tools:
        blocks.append(_build_code_map_block(role, query))
    if "search_knowledge" in tools and not ("search_code" in tools and _pack_id(role) == "cartographer"):
        blocks.append(_build_role_knowledge_block(role, query))
    return "\n\n".join(block for block in blocks if block) or None


def _build_code_map_block(role: RoleSpec, query: str) -> str:
    from knowledge_pack.cartographer import render_codebase_context

    prefix = "## Code map search\n\n"
    path = get_knowledge_dir() / "cartographer.sqlite"
    if not path.is_file():
        return prefix + "Notice: Indexed code map is not available."
    try:
        pack = KnowledgePack.open(path)
        roots = resolve_pack_roots(pack) or {}
        if role.repos:
            roots = {name: root for name, root in roots.items() if name in role.repos}
        commits = pack.info().commits
        stale = any(snapshot_is_stale(root, commits.get(name)) for name, root in roots.items())
        notice = "Notice: Indexed code map is stale.\n\n" if stale else ""
        hits = [hit for hit in pack.search(query, k=16) if hit.repo in roots][:3]
        context = render_codebase_context(hits, roots, max_tokens=800 - len((prefix + notice).encode()))
        return prefix + notice + (context or "No verifiable indexed map/contract matches are available.")
    except Exception as exc:  # noqa: BLE001 — retrieval failure must not fail the chat turn
        log.warning("Indexed code map retrieval unavailable: %s", type(exc).__name__)
        return prefix + "Notice: Indexed code map could not be read."


def _build_role_knowledge_block(role: RoleSpec | None, query: str) -> str | None:
    """Retrieve up to 8 passages from role's knowledge pack and format as cited block (Issue #1479).

    Precondition: role is optional.
    Postcondition: returns markdown string starting with '## Knowledge' or None.
    Never raises exceptions (orthogonality).
    """
    if role is None:
        return None

    all_tools = _tools(role)
    if "search_knowledge" not in all_tools:
        return None

    pack_id = _pack_id(role)

    pack_path = get_knowledge_dir() / f"{pack_id}.sqlite"
    if not pack_path.is_file():
        return f"## Knowledge\n\nNotice: Knowledge pack '{pack_id}' is not available."

    try:
        pack = KnowledgePack.open(pack_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed opening knowledge pack '%s': %s", pack_id, exc)
        return f"## Knowledge\n\nNotice: Knowledge pack '{pack_id}' could not be opened."

    stale = pack_is_stale(pack)

    if pack_id == "cartographer":
        from knowledge_pack.cartographer import preload_panel_context

        try:
            roots = resolve_pack_roots(pack)
            context = preload_panel_context(roots, query, max_tokens=800, topic_only=True) if roots else ""
        except (OSError, ValueError, KeyError):
            context = ""
        return "## Knowledge\n\n" + (context or "Notice: No verifiable codebase passages are available.")

    try:
        passages = pack.search(query, k=8)
    except Exception as exc:  # noqa: BLE001
        log.warning("Search query '%s' failed on pack '%s': %s", query, pack_id, exc)
        passages = []

    lines = ["## Knowledge\n"]
    if stale:
        lines.append(f"Notice: Knowledge pack '{pack_id}' is stale.\n")

    if passages:
        for p in passages:
            lines.append(f"### {p.citation}\n{p.text}\n")
    else:
        lines.append("No matching passages found.\n")

    return "\n".join(lines).strip()
