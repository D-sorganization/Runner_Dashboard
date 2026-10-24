"""Fleet security posture assembly (epic #2566, issues #2567 and #2570).

For each repository the dashboard tracks (``config/projects.json`` plus any repo the
operator config names), this module reads the ``.oss-scanner/`` contract from the
default branch, validates it with the vendored Repository_Management validator,
joins open finding counts from the private store, and, on request, open GitHub
security-alert counts.

Postconditions: nothing here raises for one repository's problem. A GitHub failure
becomes ``error`` on that repository's entry, so the routes never answer 5xx because
one repository is bad. Contract reads are cached for ``CONTRACT_TTL_SECONDS`` and
alert counts for the operator ``alerts_ttl_seconds``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cache_utils import cache_get, cache_set
from dashboard_config import ORG
from fastapi import HTTPException
from projects.service import configured_repos, fetch_repo_file
from vuln_scan import github_alerts
from vuln_scan.contract import PROJECT_PATH, check_contract
from vuln_scan.findings import get_findings_store
from vuln_scan.operator_config import OperatorConfig, load_operator_config

log = logging.getLogger("dashboard.vuln_scan.service")

CONTRACT_TTL_SECONDS = 1800.0
_CONCURRENCY = 4


def _fetch() -> github_alerts.Fetcher:
    """Resolved at call time so tests can monkeypatch ``service.gh_api``."""
    from gh_utils import gh_api

    return gh_api


def tracked_repos(config: OperatorConfig | None = None) -> list[str]:
    """Projects-tab repositories, then extra repositories the operator config names."""
    config = config or load_operator_config()
    repos = configured_repos()
    return repos + [r for r in sorted(config.repos) if r not in repos]


async def _path_size(repo: str, path: str, fetch: github_alerts.Fetcher) -> int | None:
    try:
        payload = await fetch(f"/repos/{ORG}/{repo}/contents/{path}")
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    size = payload.get("size") if isinstance(payload, dict) else None
    return size if isinstance(size, int) else None


async def contract_status(
    repo: str, fetch: github_alerts.Fetcher | None = None
) -> dict[str, Any]:
    """One repository's contract report (cached); GitHub failures land in ``error``."""
    key = f"vuln_scan:contract:{repo}"
    cached = cache_get(key, CONTRACT_TTL_SECONDS)
    if cached is not None:
        return cached
    fetch = fetch or _fetch()
    try:
        text = await fetch_repo_file(repo, PROJECT_PATH, fetch)
        files: dict[str, int | None] = {}
        if text is not None:
            first = check_contract(org=ORG, repo=repo, project_yaml=text, files={})
            for path in {first.dockerfile, first.threat_model}:
                if path is not None:
                    files[path] = await _path_size(repo, path, fetch)
        report = check_contract(
            org=ORG, repo=repo, project_yaml=text, files=files
        ).to_dict()
    except HTTPException as exc:
        log.warning("contract read failed for %s: %s", repo, exc.detail)
        return {
            "repo": repo,
            "present": False,
            "valid": False,
            "problems": [],
            "error": f"GitHub HTTP {exc.status_code}",
        }
    cache_set(key, report)
    return report


async def alert_status(
    repo: str, config: OperatorConfig, fetch: github_alerts.Fetcher | None = None
) -> dict[str, Any]:
    key = f"vuln_scan:alerts:{repo}"
    cached = cache_get(key, float(config.alerts_ttl_seconds))
    if cached is not None:
        return cached
    counts = await github_alerts.alert_counts(ORG, repo, fetch or _fetch())
    cache_set(key, counts)
    return counts


async def _gather(repos: list[str], fn: Any) -> list[Any]:
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(repo: str) -> Any:
        async with gate:
            return await fn(repo)

    return await asyncio.gather(*(one(r) for r in repos))


async def contracts_overview(repos: list[str] | None = None) -> dict[str, Any]:
    config = load_operator_config()
    repos = repos if repos is not None else tracked_repos(config)
    reports = await _gather(repos, contract_status)
    summary = {
        "repos": len(reports),
        "enrolled": sum(1 for r in reports if r.get("present")),
        "valid": sum(1 for r in reports if r.get("valid")),
        "invalid": sum(1 for r in reports if r.get("present") and not r.get("valid")),
        "errors": sum(1 for r in reports if r.get("error")),
    }
    return {
        "contracts": reports,
        "summary": summary,
        "cache_ttl_seconds": CONTRACT_TTL_SECONDS,
    }


async def coverage_overview() -> dict[str, Any]:
    """Which repositories are enrolled, enabled, scanned and due (#2574)."""
    from datetime import UTC, datetime

    from vuln_scan.findings import default_db_path
    from vuln_scan.scans import get_scan_log
    from vuln_scan.schedule import coverage_rows, coverage_summary

    config = load_operator_config()
    repos = tracked_repos(config)
    reports = await _gather(repos, contract_status)
    contracts = dict(zip(repos, reports, strict=True))
    latest = get_scan_log(default_db_path()).latest_by_repo()
    rows = coverage_rows(config, repos, contracts, latest, datetime.now(UTC))
    return {
        "coverage": rows,
        "summary": coverage_summary(rows),
        "schedule": config.schedule.to_dict(),
    }


async def posture_overview(*, include_alerts: bool = True) -> dict[str, Any]:
    """Per-repository posture: contract, operator settings, findings and alerts."""
    config = load_operator_config()
    repos = tracked_repos(config)
    contracts = await _gather(repos, contract_status)
    alerts: list[Any] = [None] * len(repos)
    if include_alerts:
        alerts = await _gather(repos, lambda r: alert_status(r, config))
    findings = get_findings_store().open_counts()
    entries = [
        {
            "repo": repo,
            "contract": contract,
            "operator": config.repo(repo).to_dict(),
            "open_findings": findings.get(repo, {}),
            "github_alerts": alert,
        }
        for repo, contract, alert in zip(repos, contracts, alerts, strict=True)
    ]
    return {
        "repos": entries,
        "operator": {
            "scan_hosts": [h.to_dict() for h in config.scan_hosts],
            "real_scans_possible": any(
                h.eligible_for_real_scans for h in config.scan_hosts
            ),
            "errors": list(config.errors),
        },
    }
