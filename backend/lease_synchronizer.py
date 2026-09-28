"""Sync GitHub claim labels and lease comments into internal runner leases (Wave 3)."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from identity import identity_manager
from issue_inventory import _parse_agent_claim, _parse_claim_expiry
from runner_lease import lease_manager

log = logging.getLogger("dashboard.lease_sync")


def _parse_iso_ts(ts_str: str) -> float | None:
    try:
        # Expected format from _parse_claim_expiry is often ISO-ish, e.g. 2026-04-26T12:00:00Z
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


async def sync_github_leases(issues: list[dict[str, Any]]):
    """Scan issues for claims and update internal leases."""
    now = time.time()
    for issue in issues:
        # GitHub REST gives label objects; the issue inventory flattens them to names.
        labels = [lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in issue.get("labels", [])]
        agent, label = _parse_agent_claim(labels)
        if not agent:
            continue

        # We have a claim. Look for expiry in body.
        expiry_str = _parse_claim_expiry(issue.get("body", ""))
        if not expiry_str:
            continue

        expires_at = _parse_iso_ts(expiry_str)
        if not expires_at or expires_at < now:
            continue

        # Principal mapping: we assume the 'agent' name maps to a principal ID
        # or we use a fallback principal if it's an external agent.
        principal_id = f"agent:{agent}"
        principal = identity_manager.get_principal(principal_id)
        if not principal:
            # For Wave 3, we only sync known principals defined in principals.yml
            log.debug("Unknown agent principal %s for issue %s", principal_id, issue.get("number"))
            continue

        # Runner mapping: GitHub claims often don't specify the runner ID.
        # We use a virtual ID keyed by issue number to track this as a quota slot.
        runner_id = f"github-claim-{issue.get('number')}"

        try:
            lease_manager.acquire_lease(
                principal=principal,
                runner_id=runner_id,
                duration_seconds=int(expires_at - now),
                task_id=f"issue-{issue.get('number')}",
                metadata={"source": "github-claim", "issue_url": issue.get("html_url")},
            )
        except (ValueError, PermissionError) as exc:
            log.debug("Skipping lease sync for %s: %s", runner_id, exc)
