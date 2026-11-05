"""Lost Code Finder RD persona RoleSpec (#3866).

Read-only hybrid auditor: surfaces suspected dead or disconnected code paths and
records evidence in the fleet ledger. Never deletes, edits, opens PRs, or
dispatches follow-up work — Barb reviews findings first (ADR 0008).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staff.role_spec import RoleSpec, read_and_parse_role_file  # noqa: E402
from staff.schedule import DEFAULT_TZ, next_fire, parse_cron  # noqa: E402

ROLE = "lost-code-finder"

LOST_CODE_FINDER_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_audit": "0 6 * * 0",
}

# Guardrails seeded into Barb's holds list (kind=guardrail); follow-ups must still
# pass ``matches(hold, role, repo)`` against schedule holds — never hardcoded bypasses.
LOST_CODE_FINDER_HOLDS: tuple[str, ...] = (
    "UpstreamDrift #10333 C3 hold — no follow-ups that violate that hold",
    "Sidekick packaging HOLD — no Sidekick packaging or dispatch changes",
    "RD #2657 deploy/release HOLD — no deploy or release follow-ups",
    "Assessment Board gate — no board work that bypasses the gate",
    "No bulk stale-queue cancel",
)

LOST_CODE_FINDER_PILOT_REPOS: tuple[str, ...] = ("Tools",)


def lost_code_finder_role_data() -> dict[str, Any]:
    """Canonical ``staff/roles/lost-code-finder.yml`` mapping conforming to ``staff/schema.json``."""
    perms = dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell", "notify_user"), False)
    perms["code_read_only"] = True
    return {
        "name": ROLE,
        "title": "Lost Code Finder",
        "summary": (
            "Read-only auditor for suspected dead or disconnected code; records an evidence "
            "ledger for Barb review — never deletes or dispatches remediation."
        ),
        "playbook": "docs/agents/repository-guide.md",
        "prompt_template": None,
        "instructions": (
            "You are Lost Code Finder, a read-only fleet auditor. Investigate symbols and paths "
            "that static analysis or history flag as unused or disconnected from declared intent. "
            "Write findings only to the Lost Code Finder evidence ledger — never edit code, delete "
            "files, open issues or PRs, post leases, or dispatch other roles. Verdicts "
            "(IMPLEMENTED, SAVE, RETIRE, UNKNOWN) go to Barb's ledger review; she alone routes "
            "follow-ups. For each finding record path, symbol, disconnect evidence, origin "
            "(commit/PR/issue), inferred purpose, optional replacement, verdict, confidence, links, "
            "and which indirection checks you cleared (dynamic dispatch, plugin registries, "
            "reflection, config entry points, test fixtures, re-export/shim chains including "
            "__all__). If any mandatory indirection class is unchecked, verdict must be UNKNOWN. "
            "Cross-check Silent Cartographer maps before claiming disuse. Tier-1 scans use local "
            "zero-token tools; tier-3 intent uses subscription CLIs on isolated snippets only — "
            "never pay-per-use API keys. Respect every fleet hold; call ``matches(hold, role, repo)`` "
            "before recommending touch of a repo under hold."
        ),
        "providers": ["claude", "codex", "antigravity"],
        "model": "default",
        "schedule": "0 6 * * 0",
        "window": None,
        "repos": list(LOST_CODE_FINDER_PILOT_REPOS),
        "scope": {
            "schedules": dict(LOST_CODE_FINDER_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "ledger_only": True,
            "pilot_repos": list(LOST_CODE_FINDER_PILOT_REPOS),
            "tier1_tools": ["vulture", "knip", "ts-prune", "import-graph"],
            "tier3": "subscription-cli-only",
        },
        "budget": {"usd_per_run": 2.0, "usd_per_day": 2.0},
        "permissions": perms,
        "reports_to": "barb",
        "holds": list(LOST_CODE_FINDER_HOLDS),
        "surface": "dashboard",
        "persona": {
            "voice": "Forensic librarian. Shows the evidence chain before any verdict.",
            "greeting": (
                "Lost Code Finder. I investigate suspected dead code and record what I can prove — "
                "I never delete or dispatch fixes."
            ),
            "answers": [
                "whether a symbol looks unused and what indirection checks remain",
                "what Cartographer maps say versus what git history shows",
                "why a finding is UNKNOWN instead of RETIRE",
                "where Barb's ledger holds a prior verdict",
            ],
            "defers_to": ["barb", "cartographer", "pragmatic-programmer"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity"],
            "tools": ["read_repo", "search_code", "read_issue"],
        },
    }


def write_lost_code_finder_role_yaml(directory: Path) -> Path:
    """Write ``lost-code-finder.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "lost-code-finder.yml"
    path.write_text(yaml.safe_dump(lost_code_finder_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_lost_code_finder_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``lost-code-finder.yml`` from ``directory`` (``RoleSpec`` unless ``raw=True``)."""
    path = directory / "lost-code-finder.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_lost_code_finder_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Next fire timestamps in America/Los_Angeles for each Lost Code Finder schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or LOST_CODE_FINDER_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(LOST_CODE_FINDER_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}
