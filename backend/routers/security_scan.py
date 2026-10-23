"""Fleet security scanner API (epic #2566; issues #2567, #2569, #2570).

``GET  /api/v1/security/contracts``          per-repo ``.oss-scanner/`` contract status
``GET  /api/v1/security/contracts/{repo}``   one repository's contract status
``GET  /api/v1/security/config``             operator settings (``config/security_scan.json``)
``GET  /api/v1/security/posture``            contract + open findings + GitHub alert counts
``GET  /api/v1/security/findings``           private findings (``security.read``)
``GET  /api/v1/security/findings/{id}``      one finding with its triage audit trail
``POST /api/v1/security/findings/{id}/triage``  change state (``security.triage``, audited)
``POST /api/v1/security/findings/{id}/advisory``  draft GHSA with a private fork (``security.advisory``)
``GET  /api/v1/security/export/sarif``       SARIF 2.1.0 of open findings (opt-in, ``security.read``)
``POST /api/v1/security/scans``              plan (dry run, default) or start a scan (``security.scan``)
``GET  /api/v1/security/scans``              recent scan runs
``GET  /api/v1/security/scans/{id}``         one scan run
``POST /api/v1/security/scans/{id}/cancel``  stop a running scan (``security.scan``)

Contract status mirrors the Projects tab: contracts are public repository files, so
those reads are gated like the fleet routes (``require_fleet_peer``). Findings and
posture are not public, so they need ``security.read``. Until identity presets grant
the ``security.*`` scopes, only admin principals (``*``) hold them.

A scan always targets the default-branch head; the request cannot name a SHA. A dry
run (the default) evaluates admission and records a ``planned`` row without touching
docker. A real scan starts only when every admission rule passes (VM scan host, idle
CI, night window, budget; see ``vuln_scan.admission``).

The one GitHub write is the advisory route: an operator click turns one confirmed
high or critical finding into a draft advisory (``vuln_scan.advisories``). Every
finding response carries ``patch_protected_paths``; a patch that touches
``.github/`` or ``.oss-scanner/`` is never handed to automation.
"""

from __future__ import annotations

import re
from typing import Any

from dashboard_config import ORG
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from identity import Principal, require_fleet_peer, require_scope
from pydantic import BaseModel, Field
from vuln_scan import advisories, scan_service, service
from vuln_scan.findings import SEVERITIES, STATES, FindingError, get_findings_store
from vuln_scan.operator_config import load_operator_config
from vuln_scan.patches import protected_paths
from vuln_scan.sarif import to_sarif

router = APIRouter(prefix="/api/v1/security", tags=["security"])

_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_ID = re.compile(r"^[0-9a-f]{32}$")
_SECURITY_READ = Depends(require_scope("security.read"))
_SECURITY_TRIAGE = Depends(require_scope("security.triage"))
_SECURITY_SCAN = Depends(require_scope("security.scan"))
_SECURITY_ADVISORY = Depends(require_scope("security.advisory"))
_SCAN_ID = re.compile(r"^[0-9a-f]{16}$")


def _repo_or_404(repo: str) -> str:
    if not _REPO.match(repo) or repo not in service.tracked_repos():
        raise HTTPException(status_code=404, detail=f"{repo} is not a tracked repository")
    return repo


def _id_or_404(finding_id: str) -> str:
    if not _ID.match(finding_id):
        raise HTTPException(status_code=404, detail="no such finding")
    return finding_id


def _present(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "patch_protected_paths": protected_paths(row.get("patch") or "")}


def _advisory_store() -> advisories.AdvisoryStore:
    return advisories.AdvisoryStore(get_findings_store().path)


def _writer() -> advisories.Writer:
    """Resolved at call time so tests can monkeypatch ``gh_utils.gh_api_write``."""
    from gh_utils import gh_api_write

    return gh_api_write


class ScanRequestBody(BaseModel):
    repo: str = Field(..., max_length=100)
    dry_run: bool = Field(True, description="evaluate admission only (default)")


class TriageRequest(BaseModel):
    state: str = Field(..., description=f"one of {', '.join(STATES)}")
    note: str = Field("", max_length=2000)


@router.get("/contracts")
async def list_contracts(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """Every tracked repository's contract status. Never 5xx for one repository."""
    return await service.contracts_overview()


@router.get("/contracts/{repo}")
async def get_contract(repo: str, _peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    return await service.contract_status(_repo_or_404(repo))


@router.get("/config")
async def get_config(_p: Principal = _SECURITY_READ) -> dict[str, Any]:
    return load_operator_config().to_dict()


@router.get("/posture")
async def get_posture(
    alerts: bool = Query(True, description="include GitHub alert counts (cached)"),
    _p: Principal = _SECURITY_READ,
) -> dict[str, Any]:
    return await service.posture_overview(include_alerts=alerts)


@router.get("/findings")
async def list_findings(
    repo: str | None = Query(None),
    state: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    _p: Principal = _SECURITY_READ,
) -> dict[str, Any]:
    if state is not None and state not in STATES:
        raise HTTPException(status_code=422, detail=f"state must be one of {STATES}")
    if severity is not None and severity not in SEVERITIES:
        raise HTTPException(status_code=422, detail=f"severity must be one of {SEVERITIES}")
    if repo is not None and not _REPO.match(repo):
        raise HTTPException(status_code=422, detail="invalid repository name")
    rows = get_findings_store().list(repo=repo, state=state, severity=severity, limit=limit)
    return {"findings": [_present(r) for r in rows], "count": len(rows)}


@router.get("/findings/{finding_id}")
async def get_finding(finding_id: str, _p: Principal = _SECURITY_READ) -> dict[str, Any]:
    store = get_findings_store()
    row = store.get(_id_or_404(finding_id))
    if row is None:
        raise HTTPException(status_code=404, detail="no such finding")
    return {
        "finding": _present(row),
        "audit": store.audit(finding_id),
        "advisory": _advisory_store().get(finding_id),
    }


@router.post("/findings/{finding_id}/triage")
async def triage_finding(
    finding_id: str,
    body: TriageRequest,
    principal: Principal = _SECURITY_TRIAGE,
) -> dict[str, Any]:
    try:
        row = get_findings_store().triage(_id_or_404(finding_id), body.state, actor=principal.id, note=body.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such finding") from exc
    except FindingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"finding": _present(row)}


@router.post("/findings/{finding_id}/advisory")
async def create_advisory(finding_id: str, principal: Principal = _SECURITY_ADVISORY) -> dict[str, Any]:
    """Draft a GitHub security advisory (private fork) for one confirmed high/critical finding."""
    row = get_findings_store().get(_id_or_404(finding_id))
    if row is None:
        raise HTTPException(status_code=404, detail="no such finding")
    store = _advisory_store()
    try:
        created = await advisories.create_draft(row, org=ORG, actor=principal.id, store=store, write=_writer())
    except advisories.AdvisoryExists as exc:
        raise HTTPException(status_code=409, detail="an advisory already exists for this finding") from exc
    except advisories.AdvisoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"advisory": created}


@router.get("/export/sarif")
async def export_sarif(
    repo: str | None = Query(None),
    _p: Principal = _SECURITY_READ,
) -> JSONResponse:
    """Open findings as SARIF 2.1.0; off unless the operator config sets ``sarif_export``."""
    if not load_operator_config().sarif_export:
        raise HTTPException(status_code=404, detail="SARIF export is off (operator config sarif_export)")
    if repo is not None and not _REPO.match(repo):
        raise HTTPException(status_code=422, detail="invalid repository name")
    rows = get_findings_store().list(repo=repo, limit=1000)
    return JSONResponse(to_sarif(rows), media_type="application/sarif+json")


@router.post("/scans")
async def request_scan(body: ScanRequestBody, principal: Principal = _SECURITY_SCAN) -> dict[str, Any]:
    repo = _repo_or_404(body.repo)
    row = await scan_service.request_scan(repo, dry_run=body.dry_run, requested_by=principal.id)
    return {"scan": row}


@router.get("/scans")
async def list_scans(
    limit: int = Query(50, ge=1, le=500),
    _p: Principal = _SECURITY_READ,
) -> dict[str, Any]:
    rows = scan_service.scan_log().recent(limit)
    return {"scans": rows, "count": len(rows)}


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: str, _p: Principal = _SECURITY_READ) -> dict[str, Any]:
    row = scan_service.scan_log().get(scan_id) if _SCAN_ID.match(scan_id) else None
    if row is None:
        raise HTTPException(status_code=404, detail="no such scan")
    return {"scan": row}


@router.post("/scans/{scan_id}/cancel")
async def cancel_scan(scan_id: str, _p: Principal = _SECURITY_SCAN) -> dict[str, Any]:
    if not _SCAN_ID.match(scan_id) or not scan_service.cancel_scan(scan_id):
        raise HTTPException(status_code=404, detail="no running scan with that id")
    return {"cancelled": scan_id}
