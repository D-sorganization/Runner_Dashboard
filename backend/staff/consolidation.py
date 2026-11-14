"""PR-consolidation strategy for staff runs (issue #1213, companion RM#1690).

A role may carry ``strategy: {consolidate_when: {open_prs: N, utilisation_pct: P}}``
in its YAML. Before a run for that role is planned, the dashboard measures the
repository's open non-draft PR count and the fleet runner utilisation and tells
the agent, in its prompt, whether to fold the eligible open PRs into ONE PR
(``consolidate``) or to drain them one at a time (``serial``).

``evaluate`` is pure. ``decide`` binds it to two injectable input fetchers and
degrades to ``serial`` with the reason ``inputs unavailable`` when either
fetch fails, so a GitHub outage never blocks a scheduled run.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import subprocess
from collections.abc import Callable
from typing import Any

from staff.roles import RoleSpec
from staff.workspace import ORG

log = logging.getLogger("dashboard.staff.consolidation")

MODE_CONSOLIDATE = "consolidate"
MODE_SERIAL = "serial"
INPUTS_UNAVAILABLE = "inputs unavailable"
THRESHOLD_KEYS = ("open_prs", "utilisation_pct")

EXCLUSIONS = (
    "draft, do-not-merge, do-not-automate, claim:local, PRs of other live sessions, workflow changes, "
    "bot snapshot PRs that delete main lines"
)
NEVER = "Never cancel or re-run other PRs' CI."

_OUTCOME_RE = re.compile(r"consolidated\s+(\d+)\s+PRs?\s+into\s+#(\d+)", re.IGNORECASE)
_GH_TIMEOUT_SECONDS = 60


def threshold(role: RoleSpec) -> dict[str, int] | None:
    """The role's ``consolidate_when`` thresholds as ints, or ``None`` when the role has no strategy.

    Post: every returned value is a non-negative int; keys outside
    ``THRESHOLD_KEYS`` are dropped (additive schema); an empty block is ``None``.
    """
    block = role.strategy.get("consolidate_when") if isinstance(role.strategy, dict) else None
    if not isinstance(block, dict):
        return None
    out: dict[str, int] = {}
    for key in THRESHOLD_KEYS:
        raw = block.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            out[key] = max(0, int(raw))
        except (TypeError, ValueError):
            log.warning("staff: role %s strategy.consolidate_when.%s=%r is not an int; ignored", role.name, key, raw)
    return out or None


def evaluate(role: RoleSpec, repo: str, open_pr_count: int, utilisation_pct: int) -> dict[str, Any]:
    """Pure decision: ``consolidate`` when every configured threshold is met, else ``serial``.

    Pre: ``open_pr_count`` and ``utilisation_pct`` are non-negative ints.
    Post: the result carries ``mode``, a human ``reason`` and the ``threshold`` used.
    """
    assert open_pr_count >= 0 and utilisation_pct >= 0, "inputs must be non-negative"  # noqa: S101
    limits = threshold(role) or {}
    facts = f"{repo or 'repo'} has {open_pr_count} open PRs, fleet utilisation {utilisation_pct}%"
    if not limits:
        return {"mode": MODE_SERIAL, "reason": f"role has no consolidate_when threshold; {facts}", "threshold": {}}
    unmet = []
    if "open_prs" in limits and open_pr_count < limits["open_prs"]:
        unmet.append(f"open PRs {open_pr_count} < {limits['open_prs']}")
    if "utilisation_pct" in limits and utilisation_pct < limits["utilisation_pct"]:
        unmet.append(f"utilisation {utilisation_pct}% < {limits['utilisation_pct']}%")
    if unmet:
        return {"mode": MODE_SERIAL, "reason": "; ".join(unmet), "threshold": limits}
    met = []
    if "open_prs" in limits:
        met.append(f"open PRs {open_pr_count} >= {limits['open_prs']}")
    if "utilisation_pct" in limits:
        met.append(f"utilisation {utilisation_pct}% >= {limits['utilisation_pct']}%")
    return {"mode": MODE_CONSOLIDATE, "reason": "; ".join(met), "threshold": limits}


def unavailable(role: RoleSpec, error: str = "") -> dict[str, Any]:
    """The fail-safe decision when an input could not be fetched."""
    reason = INPUTS_UNAVAILABLE if not error else f"{INPUTS_UNAVAILABLE} ({error[:120]})"
    return {"mode": MODE_SERIAL, "reason": reason, "threshold": threshold(role) or {}}


def prompt_paragraph(decision: dict[str, Any]) -> str:
    """The paragraph appended to the agent prompt for a decision."""
    mode = decision.get("mode", MODE_SERIAL)
    reason = decision.get("reason", "")
    if mode == MODE_CONSOLIDATE:
        action = (
            "Fold the eligible open PRs of this repository into ONE PR; "
            f"exclusions: {EXCLUSIONS}. {NEVER} Report the result on the final STAFF_RESULT: line as "
            "'consolidated N PRs into #M'."
        )
    else:
        action = (
            "Work the open PRs of this repository one at a time (serial); do not fold them into one PR; "
            f"exclusions: {EXCLUSIONS}. {NEVER}"
        )
    return f"Consolidation mode: {mode} — {reason}. {action}"


def parse_outcome(text: str) -> str:
    """Normalise ``consolidated N PRs into #M`` from a ``STAFF_RESULT:`` line; empty when absent."""
    match = _OUTCOME_RE.search(text or "")
    if match is None:
        return ""
    count, number = int(match.group(1)), int(match.group(2))
    return f"consolidated {count} PR{'' if count == 1 else 's'} into #{number}"


# ── input fetchers (injectable) ──────────────────────────────────────────
def open_pr_count(repo: str) -> int:
    """Open non-draft PRs of ``ORG/repo`` via the ``gh`` CLI (same query as ``routers/repos.py``)."""
    assert repo, "repo is required"  # noqa: S101
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", "--paginate", f"/repos/{ORG}/{repo}/pulls?state=open&per_page=100"],
        capture_output=True,
        text=True,
        timeout=_GH_TIMEOUT_SECONDS,
        check=True,
    )
    return sum(1 for pr in _concatenated_json(proc.stdout) if isinstance(pr, dict) and not pr.get("draft"))


def _concatenated_json(text: str) -> list[Any]:
    """Flatten the ``[...][...]`` stream ``gh api --paginate`` prints (one array per page)."""
    decoder = json.JSONDecoder()
    items: list[Any] = []
    pos = 0
    text = text or ""
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            return items
        page, pos = decoder.raw_decode(text, pos)
        items.extend(page if isinstance(page, list) else [page])


def _utilisation_from(capacity: dict[str, Any]) -> int:
    """``busy / online`` as a whole percent; 0 when nothing is online."""
    online = int(capacity.get("online_runners", 0) or 0)
    busy = int(capacity.get("busy_runners", 0) or 0)
    return int(busy / online * 100) if online > 0 else 0


def utilisation_pct() -> int:
    """Fleet utilisation from the capacity provider ``orchestrator_api`` uses (``busy/online``).

    Runs on the scheduler thread, so an async provider is driven on a private
    loop; failures propagate so ``decide`` can fall back to ``serial``.
    """
    from orchestrator_api import _capacity_provider  # noqa: PLC0415 — server wires it at import time

    result = _capacity_provider()
    if inspect.isawaitable(result):
        result = asyncio.run(result)  # type: ignore[arg-type]
    if not isinstance(result, dict):
        raise TypeError("capacity provider returned no mapping")
    return _utilisation_from(result)


def decide(
    role: RoleSpec,
    repo: str,
    *,
    pr_counter: Callable[[str], int] = open_pr_count,
    utilisation: Callable[[], int] = utilisation_pct,
) -> dict[str, Any] | None:
    """Fetch inputs and evaluate. ``None`` when the role has no strategy or no repo."""
    if not repo or threshold(role) is None:
        return None
    try:
        prs = pr_counter(repo)
        util = utilisation()
    except Exception as exc:  # noqa: BLE001 — fail safe to serial
        log.warning("staff: consolidation inputs for %s/%s unavailable: %s", role.name, repo, exc)
        return unavailable(role, str(exc))
    return evaluate(role, repo, int(prs), int(util))
