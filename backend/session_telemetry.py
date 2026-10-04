"""Session telemetry ingestion, 30-day PR metrics, and post-PR alerts (issue #1849 / RD-4).

Implements:
- Collection of session metadata: cost, context size, turns, wake-ups, post-PR spend/wake-ups,
  environment, linked PR, merge state, pre-push hook duration, and docs merge conflicts.
- Alerting on sessions exceeding thresholds: >5 wake-ups or >$20 spent after PR opened.
- 30-day trend metrics matching Repository_Management#1889 success targets.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("dashboard.session_telemetry")

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_TELEMETRY_FILE = DATA_DIR / "session_telemetry.json"

ALERT_WAKEUPS_AFTER_PR_THRESHOLD: int = 5
ALERT_COST_AFTER_PR_THRESHOLD_USD: float = 20.0


def calculate_percentile(values: list[float], pct: float) -> float:
    """Calculate percentile (0 <= pct <= 100) using linear interpolation."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 1:
        return float(sorted_v[0])
    clamped_pct = max(0.0, min(100.0, float(pct)))
    k = (n - 1) * (clamped_pct / 100.0)
    idx = int(k)
    next_idx = min(idx + 1, n - 1)
    weight = k - idx
    return float(sorted_v[idx] + weight * (sorted_v[next_idx] - sorted_v[idx]))


def evaluate_alerts(wakeups_after_pr: int, cost_after_pr_usd: float) -> tuple[bool, list[str]]:
    """Check post-PR thresholds and return (is_alert, alert_reasons)."""
    reasons: list[str] = []
    if wakeups_after_pr > ALERT_WAKEUPS_AFTER_PR_THRESHOLD:
        reasons.append(
            f"Excessive post-PR wake-ups: {wakeups_after_pr} (>{ALERT_WAKEUPS_AFTER_PR_THRESHOLD} threshold)"
        )
    if cost_after_pr_usd > ALERT_COST_AFTER_PR_THRESHOLD_USD:
        reasons.append(
            f"Excessive post-PR spend: ${cost_after_pr_usd:.2f} (>${ALERT_COST_AFTER_PR_THRESHOLD_USD:.2f} threshold)"
        )
    return (len(reasons) > 0, reasons)


@dataclass(slots=True)
class SessionRecord:
    """Normalized telemetry record for one coding or staff session."""

    session_id: str
    cost_usd: float = 0.0
    context_size: int = 0
    environment: str = "default"
    turns_count: int = 0
    wakeups_count: int = 0
    wakeups_after_pr: int = 0
    cost_after_pr_usd: float = 0.0
    origin: str = "dashboard-dispatched"
    linked_pr: int | None = None
    pr_merged: bool = False
    model: str = ""
    pre_push_duration_s: float | None = None
    docs_merge_conflicts: int = 0
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    is_alert: bool = False
    alert_reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        """Construct and validate a SessionRecord from raw mapping."""
        session_id = str(data.get("session_id", "")).strip()
        if not session_id:
            raise ValueError("session_id must be a non-empty string")

        cost_usd = float(data.get("cost_usd", 0.0))
        if cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")

        context_size = int(data.get("context_size", 0))
        if context_size < 0:
            raise ValueError("context_size must be non-negative")

        wakeups_after_pr = int(data.get("wakeups_after_pr", 0))
        if wakeups_after_pr < 0:
            raise ValueError("wakeups_after_pr must be non-negative")

        cost_after_pr_usd = float(data.get("cost_after_pr_usd", 0.0))
        if cost_after_pr_usd < 0:
            raise ValueError("cost_after_pr_usd must be non-negative")

        pre_push = data.get("pre_push_duration_s")
        pre_push_val = float(pre_push) if pre_push is not None else None

        recorded_at = str(data.get("recorded_at", "")).strip() or datetime.now(UTC).isoformat()
        is_alert, alert_reasons = evaluate_alerts(wakeups_after_pr, cost_after_pr_usd)

        return cls(
            session_id=session_id,
            cost_usd=cost_usd,
            context_size=context_size,
            environment=str(data.get("environment", "default")).strip(),
            turns_count=max(0, int(data.get("turns_count", 0))),
            wakeups_count=max(0, int(data.get("wakeups_count", 0))),
            wakeups_after_pr=wakeups_after_pr,
            cost_after_pr_usd=cost_after_pr_usd,
            origin=str(data.get("origin", "dashboard-dispatched")).strip(),
            linked_pr=int(data["linked_pr"]) if data.get("linked_pr") is not None else None,
            pr_merged=bool(data.get("pr_merged", False)),
            model=str(data.get("model", "")).strip(),
            pre_push_duration_s=pre_push_val,
            docs_merge_conflicts=max(0, int(data.get("docs_merge_conflicts", 0))),
            recorded_at=recorded_at,
            is_alert=is_alert,
            alert_reasons=alert_reasons,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_sessions(records: list[SessionRecord]) -> dict[str, Any]:
    """Aggregate the RM#1889 success metrics over ``records`` (shared by every window)."""
    merged_costs = [r.cost_usd for r in records if r.pr_merged]
    pr_records = [r for r in records if (r.linked_pr is not None or r.wakeups_after_pr > 0 or r.cost_after_pr_usd > 0)]
    post_pr_costs = [r.cost_after_pr_usd for r in pr_records]

    wakeups_gt_3 = [r.session_id for r in pr_records if r.wakeups_after_pr > 3]
    wakeups_gt_0 = [r.session_id for r in pr_records if r.wakeups_after_pr > 0]

    env_groups: dict[str, list[int]] = {}
    for r in records:
        env_groups.setdefault(r.environment, []).append(r.context_size)

    env_summary = {
        env: {
            "count": len(sizes),
            "median": int(calculate_percentile([float(s) for s in sizes], 50)),
            "p90": int(calculate_percentile([float(s) for s in sizes], 90)),
            "mean": round(sum(sizes) / len(sizes), 1),
        }
        for env, sizes in env_groups.items()
    }

    pre_pushes = [r.pre_push_duration_s for r in records if r.pre_push_duration_s is not None]
    docs_conflicts = [r.docs_merge_conflicts for r in records]

    return {
        "cost_per_merged_pr": {
            "count": len(merged_costs),
            "median_usd": round(calculate_percentile(merged_costs, 50), 2),
            "p90_usd": round(calculate_percentile(merged_costs, 90), 2),
        },
        "cost_after_pr": {
            "count": len(post_pr_costs),
            "total_usd": round(sum(post_pr_costs), 2),
            "median_usd": round(calculate_percentile(post_pr_costs, 50), 2),
            "p90_usd": round(calculate_percentile(post_pr_costs, 90), 2),
            "mean_usd": round(sum(post_pr_costs) / max(1, len(post_pr_costs)), 2),
        },
        "post_pr_wakeups": {
            "total_pr_sessions": len(pr_records),
            "sessions_gt_3_count": len(wakeups_gt_3),
            "sessions_gt_3_ids": wakeups_gt_3,
            "sessions_gt_0_count": len(wakeups_gt_0),
            "sessions_gt_0_ratio": round(len(wakeups_gt_0) / max(1, len(pr_records)), 3),
        },
        "startup_context_by_environment": env_summary,
        "pre_push_duration": {
            "count": len(pre_pushes),
            "median_s": round(calculate_percentile(pre_pushes, 50), 1),
            "p90_s": round(calculate_percentile(pre_pushes, 90), 1),
            "p95_s": round(calculate_percentile(pre_pushes, 95), 1),
        },
        "docs_merge_conflicts": {
            "total_conflicts": sum(docs_conflicts),
            "sessions_with_conflicts": len([c for c in docs_conflicts if c > 0]),
        },
    }


class SessionTelemetryStore:
    """Thread-safe persistent store for session telemetry records."""

    def __init__(self, data_file: Path | None = None, retention_days: int = 30) -> None:
        self.data_file = data_file or DEFAULT_TELEMETRY_FILE
        self.retention_days = max(1, retention_days)
        self._lock = threading.Lock()
        self._records: dict[str, SessionRecord] = self._load()

    def _load(self) -> dict[str, SessionRecord]:
        if not self.data_file.exists():
            return {}
        try:
            with self.data_file.open("r", encoding="utf-8") as f:
                content = json.load(f)
                if not isinstance(content, dict):
                    return {}
                records_raw = content.get("records", {})
                res: dict[str, SessionRecord] = {}
                for k, v in records_raw.items():
                    if isinstance(v, dict):
                        try:
                            res[k] = SessionRecord.from_dict(v)
                        except Exception:
                            continue
                return res
        except (OSError, json.JSONDecodeError):
            log.warning("Could not read session telemetry from %s; starting empty", self.data_file)
            return {}

    def _save(self) -> None:
        self._prune_expired()
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with self.data_file.open("w", encoding="utf-8") as f:
                data = {
                    "version": 1,
                    "last_updated_at": datetime.now(UTC).isoformat(),
                    "records": {k: v.to_dict() for k, v in self._records.items()},
                }
                json.dump(data, f, indent=2)
        except OSError as e:
            log.error("Failed to save session telemetry to %s: %s", self.data_file, e)

    def _prune_expired(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days * 2)
        cutoff_iso = cutoff.isoformat()
        expired = [sid for sid, rec in self._records.items() if rec.recorded_at < cutoff_iso]
        for sid in expired:
            self._records.pop(sid, None)

    def record_session(self, payload: dict[str, Any] | SessionRecord) -> SessionRecord:
        record = payload if isinstance(payload, SessionRecord) else SessionRecord.from_dict(payload)
        with self._lock:
            self._records[record.session_id] = record
            self._save()
        return record

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            return self._records.get(session_id)

    def list_sessions(self, since_days: int = 30) -> list[SessionRecord]:
        cutoff = datetime.now(UTC) - timedelta(days=max(1, since_days))
        cutoff_iso = cutoff.isoformat()
        with self._lock:
            return sorted(
                [rec for rec in self._records.values() if rec.recorded_at >= cutoff_iso],
                key=lambda r: r.recorded_at,
                reverse=True,
            )

    def get_metrics(self, window_days: int = 30) -> dict[str, Any]:
        """Aggregate metrics and 30-day trends matching RM#1889 targets."""
        records = self.list_sessions(since_days=window_days)
        return {
            "window_days": window_days,
            "total_sessions": len(records),
            **summarize_sessions(records),
            "trends_30d": self._build_daily_trends(records, window_days),
            "alerts": [r.to_dict() for r in records if r.is_alert],
        }

    def get_metrics_window(self, *, days: int, offset_days: int = 0, now: datetime | None = None) -> dict[str, Any]:
        """Aggregate records recorded in ``[now - offset - days, now - offset)`` (USE-1, #1865).

        Precondition: ``days`` >= 1 and ``offset_days`` >= 0.
        Postcondition: same aggregate keys as :meth:`get_metrics`, without trends.
        """
        if days < 1 or offset_days < 0:
            raise ValueError("days must be >= 1 and offset_days >= 0")
        end = (now or datetime.now(UTC)) - timedelta(days=offset_days)
        start = end - timedelta(days=days)
        with self._lock:
            records = [rec for rec in self._records.values() if start.isoformat() <= rec.recorded_at < end.isoformat()]
        return {"window_days": days, "total_sessions": len(records), **summarize_sessions(records)}

    def _build_daily_trends(self, records: list[SessionRecord], window_days: int) -> list[dict[str, Any]]:
        daily_buckets: dict[str, list[SessionRecord]] = {}
        for r in records:
            day = r.recorded_at[:10]
            daily_buckets.setdefault(day, []).append(r)

        today = datetime.now(UTC).date()
        trends: list[dict[str, Any]] = []
        for i in range(window_days - 1, -1, -1):
            day_str = (today - timedelta(days=i)).isoformat()
            bucket = daily_buckets.get(day_str, [])
            merged = [r.cost_usd for r in bucket if r.pr_merged]
            pre_push = [r.pre_push_duration_s for r in bucket if r.pre_push_duration_s is not None]
            post_costs = [r.cost_after_pr_usd for r in bucket]
            w_gt_3 = [r for r in bucket if r.wakeups_after_pr > 3]
            conflicts = [r.docs_merge_conflicts for r in bucket]

            trends.append(
                {
                    "date": day_str,
                    "sessions_count": len(bucket),
                    "merged_pr_cost_median": round(calculate_percentile(merged, 50), 2),
                    "cost_after_pr_total": round(sum(post_costs), 2),
                    "wakeups_after_pr_gt_3_count": len(w_gt_3),
                    "pre_push_p95_s": round(calculate_percentile(pre_push, 95), 1),
                    "docs_conflicts_total": sum(conflicts),
                }
            )
        return trends


_GLOBAL_TELEMETRY_STORE = SessionTelemetryStore()


def get_session_telemetry_store() -> SessionTelemetryStore:
    return _GLOBAL_TELEMETRY_STORE
