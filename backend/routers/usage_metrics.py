"""Usage metrics and telemetry router for Runner Dashboard (issue #1302 / SC-G1).

Collects local, anonymous page-view and endpoint access telemetry across nodes
over a rolling 14-day window to provide evidence for SC-G tab pruning and merges.
Can be disabled via the DASHBOARD_USAGE_METRICS_ENABLED environment variable.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from gh_client import GhClientError
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from session_telemetry import SessionTelemetryStore, get_session_telemetry_store
from usage_report import DEFAULT_REPORT_REPOSITORY, GhIssueCommentClient, build_weekly_report, post_weekly_usage_report

log = logging.getLogger("dashboard.usage_metrics")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DATA_FILE = DATA_DIR / "usage_metrics.json"

TAB_RECOMMENDATIONS: dict[str, tuple[str, str]] = {
    "overview": ("keep", "Primary landing page: fleet and runner health monitoring"),
    "queue": ("keep", "Active queue management and workflow runs"),
    "staff": ("keep", "Staff Console: AI agent team interaction (SC-D default route)"),
    "remediation": ("keep", "Failed runs triage and remediation actions"),
    "operations": (
        "keep",
        "Fleet operations: deployment, admission, runner hours, scheduled workflows, and diagnostics",
    ),
    "workflows": ("keep", "Workflow catalog and run inspection"),
    "machines": ("keep", "Multi-node runner machine inventory and capacity"),
    "events": ("keep", "Durable fleet event history and alarm center"),
    "credentials": ("keep", "Credentials and provider key administration"),
    "principals": ("keep", "RBAC roles and principal token administration"),
    "reports": ("merge", "SC-G4: Merge duplicate Reports and Analysis tabs into unified Insights"),
    "analysis": ("merge", "SC-G4: Merge duplicate Reports and Analysis tabs into unified Insights"),
    "assessments": ("merge", "Merge Code Quality Assessments into unified Insights section"),
    "deployment": ("merge", "SC-G3: Merge Deployment into Fleet Operations section"),
    "fleet-orchestration": ("merge", "SC-G3: Merge Fleet Orchestration into Fleet Operations section"),
    "fleet-command": ("merge", "SC-G3: Merge Fleet Command into Fleet Operations section"),
    "diagnostics": ("merge", "SC-G3: Merge Diagnostics into Fleet Operations section"),
    "conductor": ("merge", "SC-G3: Merge Conductor into Fleet Operations section"),
    "runner-schedule": ("merge", "SC-G3: Merge Runner Plan into Fleet Operations section"),
    "scheduled-jobs": ("merge", "SC-G3: Merge Scheduled Jobs into Fleet Operations section"),
    "runner-audit": ("merge", "Merge Runner Audit into Fleet Operations section"),
    "maxwell": ("merge", "SC-D11: Fold standalone Maxwell chat into Staff Console"),
    "agent-dispatch": ("merge", "Fold standalone agent launcher into Staff Console"),
    "code-requests": ("merge", "Merge Code Requests into Remediation or Backlog"),
    "feature-requests": ("merge", "Merge Feature Requests into Remediation or Backlog"),
    "linear-setup": ("merge", "Merge Linear setup into Settings"),
    "push-settings": ("merge", "Merge Web Push settings into Settings"),
    "cline-launcher": ("retire", "SC-G6: Legacy Cline launcher superseded by Staff Console and Conductor"),
    "local-apps": ("owner-decision", "Local process monitor; low usage, pending owner evaluation"),
    "heavy-tests": ("owner-decision", "Long-running benchmark runner; low usage, pending owner evaluation"),
}


class PageViewPayload(BaseModel):
    """Payload sent by the frontend shell on tab navigation."""

    tab_id: str = Field(..., min_length=1, max_length=100, description="Nav item identifier")
    pathname: str | None = Field(default=None, max_length=200, description="Browser path")


class SessionTelemetryPayload(BaseModel):
    """Session telemetry payload (issue #1849 / RD-4)."""

    session_id: str = Field(..., min_length=1, max_length=200, description="Session ID")
    cost_usd: float = Field(default=0.0, ge=0.0, description="Total session cost")
    context_size: int = Field(default=0, ge=0, description="Context tokens before first prompt")
    environment: str = Field(default="default", max_length=100, description="Execution environment")
    turns_count: int = Field(default=0, ge=0, description="Number of turns")
    wakeups_count: int = Field(default=0, ge=0, description="Total wake-up count")
    wakeups_after_pr: int = Field(default=0, ge=0, description="Wake-ups after PR opened")
    cost_after_pr_usd: float = Field(default=0.0, ge=0.0, description="Cost spent after PR opened")
    origin: str = Field(default="dashboard-dispatched", max_length=100, description="Origin")
    linked_pr: int | None = Field(default=None, ge=1, description="Linked PR")
    pr_merged: bool = Field(default=False, description="Whether PR merged")
    model: str = Field(default="", max_length=100, description="Model identifier")
    pre_push_duration_s: float | None = Field(default=None, ge=0.0, description="Pre-push hook duration in seconds")
    docs_merge_conflicts: int = Field(default=0, ge=0, description="Docs merge conflict count")
    recorded_at: str | None = Field(default=None, description="ISO timestamp of record")


class UsageTracker:
    """Thread-safe persistent usage metrics accumulator with rolling-window retention."""

    def __init__(
        self,
        data_file: Path | None = None,
        retention_days: int = 14,
        enabled: bool | None = None,
    ) -> None:
        self.data_file = data_file or DEFAULT_DATA_FILE
        self.retention_days = max(1, retention_days)
        self._enabled = enabled
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._load()

    def is_enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        env_val = os.environ.get("DASHBOARD_USAGE_METRICS_ENABLED", "true").strip().lower()
        return env_val not in ("0", "false", "no", "off", "disabled")

    def _today_str(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _load(self) -> dict[str, Any]:
        if not self.data_file.exists():
            return {
                "version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "daily": {},
            }
        try:
            with self.data_file.open("r", encoding="utf-8") as f:
                content = json.load(f)
                if not isinstance(content, dict):
                    return {"version": 1, "created_at": datetime.now(UTC).isoformat(), "daily": {}}
                if "daily" not in content or not isinstance(content["daily"], dict):
                    content["daily"] = {}
                return content
        except (OSError, json.JSONDecodeError):
            log.warning("Could not read usage metrics from %s; resetting", self.data_file)
            return {"version": 1, "created_at": datetime.now(UTC).isoformat(), "daily": {}}

    def _save(self) -> None:
        self._prune_expired()
        self._data["last_updated_at"] = datetime.now(UTC).isoformat()
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with self.data_file.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            log.error("Failed to save usage metrics to %s: %s", self.data_file, e)

    def _prune_expired(self) -> None:
        cutoff = (datetime.now(UTC).date() - timedelta(days=self.retention_days)).isoformat()
        daily = self._data.get("daily", {})
        expired = [date_key for date_key in daily if date_key < cutoff]
        for key in expired:
            daily.pop(key, None)

    def record_page_view(self, tab_id: str) -> int:
        if not self.is_enabled():
            return 0
        cleaned_tab = tab_id.strip()
        if not cleaned_tab:
            return 0

        today = self._today_str()
        with self._lock:
            daily = self._data.setdefault("daily", {})
            day_data = daily.setdefault(today, {"page_views": {}, "endpoint_calls": {}})
            views = day_data.setdefault("page_views", {})
            views[cleaned_tab] = views.get(cleaned_tab, 0) + 1
            new_count = views[cleaned_tab]
            self._save()
            return new_count

    def record_api_call(self, method: str, endpoint: str) -> int:
        if not self.is_enabled():
            return 0
        normalized = f"{method.upper()} {endpoint.strip()}"
        today = self._today_str()
        with self._lock:
            daily = self._data.setdefault("daily", {})
            day_data = daily.setdefault(today, {"page_views": {}, "endpoint_calls": {}})
            calls = day_data.setdefault("endpoint_calls", {})
            calls[normalized] = calls.get(normalized, 0) + 1
            new_count = calls[normalized]
            self._save()
            return new_count

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            self._prune_expired()
            daily = self._data.get("daily", {})
            total_views: dict[str, int] = {}
            total_calls: dict[str, int] = {}

            for day_data in daily.values():
                for tab, cnt in day_data.get("page_views", {}).items():
                    total_views[tab] = total_views.get(tab, 0) + cnt
                for ep, cnt in day_data.get("endpoint_calls", {}).items():
                    total_calls[ep] = total_calls.get(ep, 0) + cnt

            # Sort descending by count
            sorted_views = dict(sorted(total_views.items(), key=lambda item: item[1], reverse=True))
            sorted_calls = dict(sorted(total_calls.items(), key=lambda item: item[1], reverse=True))

            # Build tab analysis
            all_tabs = set(TAB_RECOMMENDATIONS.keys()) | set(sorted_views.keys())
            tab_analysis: list[dict[str, Any]] = []
            for tab in sorted(all_tabs):
                views = sorted_views.get(tab, 0)
                rec, reason = TAB_RECOMMENDATIONS.get(tab, ("owner-decision", "Unregistered or dynamic tab"))
                tab_analysis.append(
                    {
                        "tab_id": tab,
                        "views": views,
                        "recommendation": rec,
                        "notes": reason,
                    }
                )

            # Sort tab analysis by views desc, then tab_id asc
            tab_analysis.sort(key=lambda x: (-x["views"], x["tab_id"]))

            today = datetime.now(UTC).date()
            window_start = (today - timedelta(days=self.retention_days)).isoformat()

            return {
                "enabled": self.is_enabled(),
                "retention_days": self.retention_days,
                "window_start": window_start,
                "window_end": today.isoformat(),
                "total_page_views": sum(sorted_views.values()),
                "total_api_calls": sum(sorted_calls.values()),
                "page_views": sorted_views,
                "endpoint_calls": sorted_calls,
                "tab_analysis": tab_analysis,
                "daily_summary": daily,
            }

    def get_markdown_table(self) -> str:
        summary = self.get_summary()
        lines = [
            f"# Dashboard Tab Usage and Disposition Analysis ({summary['window_start']} to {summary['window_end']})",
            "",
            f"- **Tracking Enabled:** `{summary['enabled']}`",
            f"- **Observation Window:** {summary['retention_days']} days",
            f"- **Total Page Views:** {summary['total_page_views']}",
            f"- **Total Endpoint Calls:** {summary['total_api_calls']}",
            "",
            "| Tab ID | Views | Recommendation | Notes |",
            "|---|:---:|:---:|---|",
        ]
        for item in summary["tab_analysis"]:
            lines.append(f"| `{item['tab_id']}` | {item['views']} | {item['recommendation']} | {item['notes']} |")
        lines.append("")
        return "\n".join(lines)


_GLOBAL_TRACKER = UsageTracker()


class WeeklyUsageReportRequest(BaseModel):
    """Body for ``POST /api/usage/report/weekly`` (USE-1, #1865)."""

    repository: str = Field(
        default=DEFAULT_REPORT_REPOSITORY,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        max_length=200,
        description="owner/name of the repository holding the report issue",
    )
    dry_run: bool = Field(default=False, description="Render only; do not touch GitHub")


def get_usage_tracker() -> UsageTracker:
    return _GLOBAL_TRACKER


def create_usage_metrics_router(
    tracker: UsageTracker | None = None,
    telemetry_store: SessionTelemetryStore | None = None,
) -> APIRouter:
    r = APIRouter(tags=["usage-metrics"])

    @r.post("/api/usage/page-view")
    async def post_page_view(payload: PageViewPayload) -> dict[str, Any]:
        active_tracker = tracker if tracker is not None else get_usage_tracker()
        if not active_tracker.is_enabled():
            return {"status": "disabled", "tab_id": payload.tab_id, "count": 0}

        count = active_tracker.record_page_view(payload.tab_id)
        return {"status": "recorded", "tab_id": payload.tab_id, "count": count}

    @r.get("/api/usage/summary")
    async def get_usage_summary() -> dict[str, Any]:
        active_tracker = tracker if tracker is not None else get_usage_tracker()
        active_store = telemetry_store if telemetry_store is not None else get_session_telemetry_store()
        summary_data = active_tracker.get_summary()
        summary_data["session_metrics"] = active_store.get_metrics(window_days=30)
        return summary_data

    @r.get("/api/usage/table")
    async def get_usage_table() -> Response:
        active_tracker = tracker if tracker is not None else get_usage_tracker()
        table_md = active_tracker.get_markdown_table()
        return Response(content=table_md, media_type="text/markdown")

    @r.post("/api/usage/session-telemetry")
    async def post_session_telemetry(payload: SessionTelemetryPayload) -> dict[str, Any]:
        active_store = telemetry_store if telemetry_store is not None else get_session_telemetry_store()
        rec = active_store.record_session(payload.model_dump())
        return rec.to_dict()

    @r.get("/api/usage/session-telemetry")
    async def get_session_telemetry(
        since_days: int = Query(default=30, ge=1, le=365, description="Days of history to return"),
    ) -> list[dict[str, Any]]:
        active_store = telemetry_store if telemetry_store is not None else get_session_telemetry_store()
        records = active_store.list_sessions(since_days=since_days)
        return [r.to_dict() for r in records]

    @r.get("/api/usage/session-metrics")
    async def get_session_metrics(
        window_days: int = Query(default=30, ge=1, le=365, description="Rolling window in days"),
    ) -> dict[str, Any]:
        active_store = telemetry_store if telemetry_store is not None else get_session_telemetry_store()
        return active_store.get_metrics(window_days=window_days)

    @r.post("/api/usage/report/weekly")
    async def post_weekly_report(
        payload: WeeklyUsageReportRequest,
        _auth: Principal = Depends(require_scope("admin")),  # noqa: B008
    ) -> dict[str, Any]:
        """Render this ISO week's usage report and create/update its comment (idempotent)."""
        active_store = telemetry_store if telemetry_store is not None else get_session_telemetry_store()
        week, body = build_weekly_report(active_store)
        if payload.dry_run:
            return {"dry_run": True, "week": week, "body": body}
        try:
            result = await post_weekly_usage_report(
                GhIssueCommentClient(), week=week, body=body, repository=payload.repository
            )
        except GhClientError as exc:
            log.warning("weekly usage report post failed: %s", exc)
            raise HTTPException(status_code=502, detail="GitHub rejected the usage report update") from exc
        return {"dry_run": False, **result.to_dict()}

    return r


router = create_usage_metrics_router()
