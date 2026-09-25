"""Knowledge-pack retrieval and citation formatting for staff chat turns (Issue #1479).

Builds the retrieval-augmented ``## Knowledge`` block injected into chat prompts
for roles whose `search_knowledge` tool is enabled. Split out of `staff.chat`
to keep that module under the repo's 500-line soft cap.
"""

from __future__ import annotations

import logging
from pathlib import Path

from knowledge_pack import KnowledgePack
from staff.knowledge_refresh import get_knowledge_dir, pack_is_stale
from staff.roles import RoleSpec

__all__ = ["build_knowledge_turn_block"]

log = logging.getLogger("dashboard.staff.chat_knowledge")


def build_knowledge_turn_block(role: RoleSpec | None, query: str) -> str | None:
    """Retrieve up to 8 passages from role's knowledge pack and format as cited block (Issue #1479).

    Precondition: role is optional.
    Postcondition: returns markdown string starting with '## Knowledge' or None.
    Never raises exceptions (orthogonality).
    """
    if role is None:
        return None

    chat_tools = list(role.chat.get("tools") or []) if isinstance(role.chat, dict) else []
    all_tools = set(role.tools) | set(chat_tools)
    if "search_knowledge" not in all_tools:
        return None

    scope = role.scope or {}
    pack_name = scope.get("pack")
    pack_id = Path(pack_name).stem if pack_name else role.name

    pack_path = get_knowledge_dir() / f"{pack_id}.sqlite"
    if not pack_path.is_file():
        return f"## Knowledge\n\nNotice: Knowledge pack '{pack_id}' is not available."

    try:
        pack = KnowledgePack.open(pack_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed opening knowledge pack '%s': %s", pack_id, exc)
        return f"## Knowledge\n\nNotice: Knowledge pack '{pack_id}' could not be opened."

    stale = pack_is_stale(pack)

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
