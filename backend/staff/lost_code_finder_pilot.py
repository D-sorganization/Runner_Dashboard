"""Lost Code Finder Tools repo weekly pilot (#3869).

Runs after RoleSpec (#3866) and ledger contract (#3867) land. Executes tier 1/2 scan
harness output into a local ledger file; tier 3 inference stays off. Exit criteria
gate rollout to additional repos.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.lost_code_finder_scan import Tier2Context, run_repo_scan

log = logging.getLogger("dashboard.staff.lost_code_finder_pilot")

ROLE = "lost-code-finder"
PILOT_REPO = "Tools"
WEEKLY_JOB = "weekly_audit"

EXIT_CRITERIA: tuple[str, ...] = (
    "citations_reproducible",
    "unknown_fail_closed",
    "indirection_checks_applied",
    "followups_unstacked_prs_via_barb",
)


def default_ledger_path() -> Path:
    override = Path.home() / ".config/runner-dashboard/lost_code_finder"
    return override / f"{PILOT_REPO.lower()}_ledger.json"


def resolve_tools_checkout(fleet_root: Path | None = None) -> Path:
    """Locate a local Tools checkout (pilot-only; no network)."""
    root = fleet_root or Path.home() / "Repositories"
    for candidate in (root / "Tools", root / "Tools-worktrees" / "main" / "Tools"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Tools checkout not found for Lost Code Finder pilot")


def run_tools_weekly_pilot(
    repo_root: Path,
    *,
    head_commit: str,
    tier1_vulture_lines: Sequence[str],
    ledger_path: Path | None = None,
    cartographer_hits: Sequence[str] = (),
) -> dict[str, Any]:
    """Run tier 1/2 scan for Tools and persist a validated ledger document."""
    doc = run_repo_scan(
        PILOT_REPO,
        repo_root,
        tier1_vulture_lines=tier1_vulture_lines,
        tier2=Tier2Context(head_commit=head_commit, cartographer_hits=cartographer_hits),
        allow_tier3=False,
    )
    doc["pilot"] = {
        "job": WEEKLY_JOB,
        "exit_criteria": list(EXIT_CRITERIA),
        "recorded_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    out = ledger_path or default_ledger_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info("lost code finder pilot wrote %s findings to %s", len(doc.get("findings", [])), out)
    return {"status": "executed", "ledger_path": str(out), "finding_count": len(doc.get("findings", []))}


def pilot_scope_matches(role_scope: Mapping[str, Any] | None) -> bool:
    """True when role scope lists the Tools pilot repo and weekly job."""
    if not isinstance(role_scope, dict):
        return False
    repos = role_scope.get("pilot_repos") or role_scope.get("repos") or []
    schedules = role_scope.get("schedules") or {}
    return PILOT_REPO in repos and WEEKLY_JOB in schedules
