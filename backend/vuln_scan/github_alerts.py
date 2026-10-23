"""Open GitHub security-alert counts per repository (epic #2566, issue #2570).

Three REST endpoints per repository are read, each paged up to
:data:`MAX_PAGES` pages: Dependabot, code scanning and secret scanning. A repository
where a feature is off or the token lacks permission answers 403/404; that kind is
reported as ``unavailable`` and is never an error for the page. Results are cached
per repository by :mod:`vuln_scan.service` for the operator ``alerts_ttl_seconds``,
so the dashboard does not fan out to GitHub on every request (fleet API hygiene).

Only counts leave this module; alert bodies, secrets and advisory text are dropped.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

log = logging.getLogger("dashboard.vuln_scan.alerts")

Fetcher = Callable[[str], Awaitable[Any]]
MAX_PAGES = 3
KINDS = {
    "dependabot": "dependabot/alerts",
    "code_scanning": "code-scanning/alerts",
    "secret_scanning": "secret-scanning/alerts",  # pragma: allowlist secret
}
_SEVERITY_ALIASES = {
    "moderate": "medium",
    "error": "high",
    "warning": "medium",
    "note": "low",
}


def _severity(kind: str, alert: dict[str, Any]) -> str:
    if kind == "dependabot":
        advisory = alert.get("security_advisory") or {}
        raw = advisory.get("severity") or (
            alert.get("security_vulnerability") or {}
        ).get("severity")
    elif kind == "code_scanning":
        rule = alert.get("rule") or {}
        raw = rule.get("security_severity_level") or rule.get("severity")
    else:
        # Secret-scanning alerts carry no severity; an exposed secret is high.
        raw = "high"
    raw = str(raw or "unknown").lower()
    return _SEVERITY_ALIASES.get(raw, raw)


async def count_kind(org: str, repo: str, kind: str, fetch: Fetcher) -> dict[str, Any]:
    """Open-alert totals for one kind, or ``{"status": "unavailable"}``."""
    endpoint = f"/repos/{org}/{repo}/{KINDS[kind]}?state=open"
    by_severity: dict[str, int] = {}
    total = 0
    try:
        for page in range(1, MAX_PAGES + 1):
            batch = await fetch(f"{endpoint}&per_page=100&page={page}")
            if not isinstance(batch, list) or not batch:
                break
            for alert in batch:
                if isinstance(alert, dict):
                    sev = _severity(kind, alert)
                    by_severity[sev] = by_severity.get(sev, 0) + 1
                    total += 1
            if len(batch) < 100:
                break
    except HTTPException as exc:
        if exc.status_code in (403, 404, 410, 422):
            return {"status": "unavailable", "reason": f"HTTP {exc.status_code}"}
        return {"status": "error", "reason": f"HTTP {exc.status_code}"}
    capped = total >= MAX_PAGES * 100
    return {"status": "ok", "open": total, "by_severity": by_severity, "capped": capped}


async def alert_counts(org: str, repo: str, fetch: Fetcher) -> dict[str, Any]:
    """All three kinds for one repository; never raises for a GitHub failure."""
    result: dict[str, Any] = {}
    for kind in KINDS:
        try:
            result[kind] = await count_kind(org, repo, kind, fetch)
        except Exception as exc:  # noqa: BLE001 - one bad repo must not break the page
            log.warning("alert count failed for %s/%s %s: %s", org, repo, kind, exc)
            result[kind] = {"status": "error", "reason": type(exc).__name__}
    return result
