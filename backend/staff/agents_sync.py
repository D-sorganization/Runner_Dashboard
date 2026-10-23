"""Compare fleet-managed AGENTS.md blocks with the canonical RM checkout."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

FetchFile = Callable[[str, str], Awaitable[str | None]]
_BLOCK = re.compile(
    r"<!-- BEGIN FLEET-MANAGED: ([a-z0-9-]+) -->\s*(.*?)\s*<!-- END FLEET-MANAGED: \1 -->",
    re.DOTALL,
)


def _managed_blocks(text: str) -> dict[str, str]:
    return {name: body.strip() for name, body in _BLOCK.findall(text)}


def _block_digests(blocks: dict[str, str]) -> dict[str, str]:
    return {name: hashlib.sha256(body.encode("utf-8")).hexdigest() for name, body in sorted(blocks.items())}


def _digest(blocks: dict[str, str]) -> str:
    canonical = json.dumps(_block_digests(blocks), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _status(rows: list[dict[str, Any]]) -> str:
    if any(row["status"] == "lagging" for row in rows):
        return "lagging"
    if any(row["status"] == "unavailable" for row in rows):
        return "unavailable"
    return "current"


async def build_agents_sync_report(fetch_file: FetchFile, repositories: list[str]) -> dict[str, Any]:
    """Compare managed sections with RM's published AGENTS.md; never infer from pack pins."""
    source = "Repository_Management/AGENTS.md"
    source_ref = "GitHub Contents API default branch"
    checked_at = datetime.now(UTC).isoformat()
    try:
        canonical_text = await fetch_file("Repository_Management", "AGENTS.md")
    except HTTPException as exc:
        reason = f"Repository metadata access is unavailable (HTTP {exc.status_code})."
        return {
            "source": source,
            "source_ref": source_ref,
            "status": "unavailable",
            "reason": reason,
            "repositories": [],
            "checked_at": checked_at,
            "source_digest": None,
            "source_blocks": None,
        }
    except Exception:
        return {
            "source": source,
            "source_ref": source_ref,
            "status": "unavailable",
            "reason": "Repository metadata access is unavailable.",
            "repositories": [],
            "checked_at": checked_at,
            "source_digest": None,
            "source_blocks": None,
        }
    canonical = _managed_blocks(canonical_text or "")
    if not canonical:
        return {
            "source": source,
            "source_ref": source_ref,
            "status": "unavailable",
            "reason": "The canonical AGENTS.md has no readable FLEET-MANAGED blocks.",
            "repositories": [],
            "checked_at": checked_at,
            "source_digest": None,
            "source_blocks": None,
        }

    if not repositories:
        return {
            "source": source,
            "source_ref": source_ref,
            "status": "unavailable",
            "reason": "No repositories are configured for sync comparison.",
            "repositories": [],
            "checked_at": checked_at,
            "source_digest": None,
            "source_blocks": None,
        }

    expected_blocks = _block_digests(canonical)
    expected_digest = _digest(canonical)
    rows: list[dict[str, Any]] = []
    for repo in sorted(set(repositories)):
        try:
            text = await fetch_file(repo, "AGENTS.md")
        except Exception:
            text = None
        if not text:
            rows.append(
                {
                    "repo": repo,
                    "status": "unavailable",
                    "changed_blocks": [],
                    "expected_blocks": expected_blocks,
                    "observed_blocks": None,
                    "expected_digest": expected_digest,
                    "observed_digest": None,
                    "last_seen_at": None,
                }
            )
            continue
        observed = _managed_blocks(text)
        observed_blocks = _block_digests(observed)
        changed = sorted(
            name for name in canonical.keys() | observed.keys() if canonical.get(name) != observed.get(name)
        )
        rows.append(
            {
                "repo": repo,
                "status": "lagging" if changed else "current",
                "changed_blocks": changed,
                "expected_blocks": expected_blocks,
                "observed_blocks": observed_blocks,
                "expected_digest": expected_digest,
                "observed_digest": _digest(observed),
                "last_seen_at": datetime.now(UTC).isoformat(),
            }
        )
    return {
        "source": source,
        "source_ref": source_ref,
        "status": _status(rows),
        "reason": None,
        "checked_at": checked_at,
        "source_digest": expected_digest,
        "source_blocks": expected_blocks,
        "repositories": rows,
    }
