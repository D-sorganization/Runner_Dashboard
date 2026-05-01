"""Daily progress report routes.

Extracted from server.py (issue #358).
Routes: GET /api/reports, GET /api/reports/{date}, GET /api/reports/{date}/chart.
"""

from __future__ import annotations

import datetime as _dt_mod
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from report_files import parse_report_metrics, sanitize_report_date

if TYPE_CHECKING:
    pass

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.reports")
router = APIRouter(tags=["reports"])

# ---------------------------------------------------------------------------
# Injected dependencies (set by server.py after import)
# ---------------------------------------------------------------------------

_reports_dir: Path | None = None


def set_reports_dir(reports_dir: Path) -> None:
    """Wire the REPORTS_DIR path from server.py into this router."""
    global _reports_dir  # noqa: PLW0603
    _reports_dir = reports_dir


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/reports")
async def list_reports() -> dict:
    """List available daily progress reports."""
    reports_dir: Path = _reports_dir  # type: ignore[assignment]
    reports = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("daily_progress_report_*.md"), reverse=True):
            date_str = f.stem.replace("daily_progress_report_", "")
            stat = f.stat()
            chart_path = reports_dir / f"assessment_scores_{date_str}.png"
            reports.append(
                {
                    "filename": f.name,
                    "date": date_str,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    "has_chart": chart_path.exists(),
                    "chart_filename": (f"assessment_scores_{date_str}.png" if chart_path.exists() else None),
                }
            )
    return {"reports": reports, "reports_dir": str(reports_dir), "total": len(reports)}


@router.get("/api/reports/{date}")
async def get_report(date: str) -> dict:
    """Get the content of a specific daily report."""
    reports_dir: Path = _reports_dir  # type: ignore[assignment]
    safe_date = sanitize_report_date(date)
    report_path = reports_dir / f"daily_progress_report_{safe_date}.md"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report not found for date: {safe_date}",
        )

    content = report_path.read_text(encoding="utf-8")
    metrics = parse_report_metrics(content)

    return {
        "date": safe_date,
        "filename": report_path.name,
        "content": content,
        "metrics": metrics,
        "size_kb": round(report_path.stat().st_size / 1024, 1),
    }


@router.get("/api/reports/{date}/chart")
async def get_report_chart(date: str) -> FileResponse:
    """Serve the assessment scores chart image for a specific date."""
    reports_dir: Path = _reports_dir  # type: ignore[assignment]
    safe_date = sanitize_report_date(date)
    chart_path = reports_dir / f"assessment_scores_{safe_date}.png"
    if not chart_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Chart not found for date: {safe_date}",
        )
    return FileResponse(chart_path, media_type="image/png")
