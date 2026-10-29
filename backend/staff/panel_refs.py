"""Fetch GitHub issue/PR facts for expert panel ``refs`` (#3313).

A caller names ``repo#n`` and RD fetches that issue or PR's own title, state and
body itself, instead of the caller pasting the facts into the topic by hand.
Removes the Grok Panel bot's own grounding-fetch step.
"""

from __future__ import annotations

import gh_client
from staff.maintenance_github import repo_full

BODY_TRUNCATE_CHARS = 1500


def _format_one(ref: str, data: dict) -> str:
    """Pre: ``data`` is a GitHub issues-API response (covers PRs too). Post: one markdown section."""
    number = data.get("number")
    title = data.get("title") or ""
    repo_state = data.get("state") or "unknown"
    url = data.get("html_url") or ""
    body = (data.get("body") or "").strip()
    if len(body) > BODY_TRUNCATE_CHARS:
        body = body[:BODY_TRUNCATE_CHARS].rstrip() + "\n…(truncated)"
    kind = "PR" if "pull_request" in data else "issue"
    return f"### {ref} — {kind} #{number}: {title} ({repo_state})\n{url}\n\n{body}"


async def fetch_refs_facts(refs: list[str]) -> str:
    """Pre: each ref is ``repo#n`` for a configured fleet repository (validated by ``PanelCreateRequest``).

    Post: one markdown section per ref, in the given order. Raises ``gh_client.GhNotFound``
    for a ref GitHub has no issue/PR for, or another ``gh_client.GhClientError`` subclass if
    GitHub itself is unavailable; the caller decides how to map those to an HTTP response.
    """
    sections = []
    for ref in refs:
        repo, _, number = ref.partition("#")
        data = await gh_client.get(f"/repos/{repo_full(repo)}/issues/{number}")
        sections.append(_format_one(ref, data))
    return "\n\n".join(sections)
