"""Assessment score and dispatch routes.

Extracted from server.py (issue #358).
Routes: GET /api/assessments/scores, POST /api/assessments/dispatch.
"""

from __future__ import annotations

import contextlib
import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from dashboard_config import ORG, REPO_ROOT
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, require_scope  # noqa: B008
from security import sanitize_log_value

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("dashboard.assessments")
router = APIRouter(tags=["assessments"])

# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_run_cmd: Callable | None = None


def set_run_cmd(run_cmd: Callable) -> None:
    """Wire server.py run_cmd helper into this router."""
    global _run_cmd  # noqa: PLW0603
    _run_cmd = run_cmd


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/assessments/scores")
async def get_assessment_scores() -> dict:
    """Return assessment score history from local assessments directory."""
    assessments_dir = REPO_ROOT / "assessments"
    results: list[dict] = []
    if assessments_dir.exists():
        for score_file in sorted(assessments_dir.rglob("*.json"), reverse=True)[:50]:
            try:
                data = json.loads(score_file.read_text(encoding="utf-8"))
                results.append(
                    {
                        "file": str(score_file.relative_to(REPO_ROOT)),
                        "repo": data.get("repository") or score_file.parent.name,
                        "score": data.get("score") or data.get("overall_score"),
                        "date": data.get("date") or data.get("timestamp") or score_file.stat().st_mtime,
                        "summary": data.get("summary") or data.get("description", "")[:200],
                        "provider": data.get("provider") or data.get("agent", ""),
                    }
                )
            except Exception as e:  # noqa: BLE001
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
    return {"scores": results, "total": len(results)}


@router.post("/api/assessments/dispatch")
async def dispatch_assessment(
    request: Request,
    *,
    principal: Principal = Depends(require_scope("assessments.dispatch")),  # noqa: B008
) -> dict:
    """Dispatch an assessment workflow for a repository."""
    body = await request.json()
    repo = str(body.get("repository", "")).strip()
    provider = str(body.get("provider", "jules_api")).strip()
    ref = str(body.get("ref", "main")).strip()
    if not repo:
        raise HTTPException(status_code=422, detail="repository required")
    if not provider:
        raise HTTPException(status_code=422, detail="provider required")

    log.info(
        "audit: assessments_dispatch repo=%s provider=%s ref=%s",
        sanitize_log_value(repo),
        sanitize_log_value(provider),
        sanitize_log_value(ref),
    )

    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/Jules-Assess-Repo.yml/dispatches"
    payload = {
        "ref": "main",
        "inputs": {"target_repository": f"{ORG}/{repo}", "provider": provider},
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        pf = f.name
    try:
        code, _, stderr = await _run_cmd(  # type: ignore[misc]
            ["gh", "api", endpoint, "--method", "POST", "--input", pf],
            timeout=30,
            cwd=REPO_ROOT,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(pf).unlink()
    if code != 0:
        log.warning("assessment dispatch failed: repo=%s stderr=%s", repo, stderr.strip()[:300])
        raise HTTPException(
            status_code=502,
            detail="Assessment dispatch failed",
        )
    return {"status": "dispatched", "repository": repo, "provider": provider}
