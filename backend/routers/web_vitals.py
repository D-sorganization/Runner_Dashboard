"""Web Vitals metrics endpoints for the runner dashboard.

Implements client-side performance metric ingestion and aggregation.
"""

from __future__ import annotations

import json
import os
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["web-vitals"])

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WEB_VITALS_PATH = DATA_DIR / "web-vitals.json"

DEFAULT_SAMPLE_RATE = 1.0 if os.environ.get("DASHBOARD_ENV") == "beta" else 0.1
SAMPLE_RATE = float(os.environ.get("DASHBOARD_WEB_VITALS_SAMPLE_RATE", DEFAULT_SAMPLE_RATE))


class WebVitalEntry(BaseModel):
    name: str = Field(..., description="Metric name: CLS, INP, FCP, LCP, etc.")
    value: float = Field(..., description="Metric value in milliseconds (or unitless for CLS)")
    rating: str = Field(default="", description="good, needs-improvement, or poor")
    delta: float | None = Field(default=None)
    id: str = Field(default="", description="Unique metric instance ID")
    navigation_type: str = Field(default="", description="navigate, reload, back-forward, etc.")


class WebVitalsPayload(BaseModel):
    route: str = Field(default="", description="Frontend route pathname")
    metrics: list[WebVitalEntry]


def _load_web_vitals() -> list[dict[str, Any]]:
    if not WEB_VITALS_PATH.exists():
        return []
    try:
        with WEB_VITALS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _save_web_vitals(data: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with WEB_VITALS_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _compute_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    try:
        if percentile == 50:
            return round(statistics.median(sorted_vals), 3)
        elif percentile == 75:
            q = statistics.quantiles(sorted_vals, n=4, method="inclusive")
            return round(q[2], 3)
        elif percentile == 95:
            q = statistics.quantiles(sorted_vals, n=20, method="inclusive")
            return round(q[18], 3)
    except statistics.StatisticsError:
        pass
    k = (len(sorted_vals) - 1) * (percentile / 100)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_vals) else f
    if f == c:
        return round(sorted_vals[f], 3)
    return round(sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f]), 3)


@router.post("/api/metrics/web-vitals")
async def post_web_vitals(payload: WebVitalsPayload, request: Request) -> dict[str, str]:
    if SAMPLE_RATE < 1.0 and random.random() > SAMPLE_RATE:
        return {"status": "sampled_out"}

    data = _load_web_vitals()
    for metric in payload.metrics:
        entry = {
            "name": metric.name,
            "value": metric.value,
            "rating": metric.rating,
            "route": payload.route or request.headers.get("referer", "unknown"),
            "delta": metric.delta,
            "id": metric.id,
            "navigation_type": metric.navigation_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "user_agent": request.headers.get("user-agent", ""),
        }
        data.append(entry)

    MAX_ENTRIES = 10000
    if len(data) > MAX_ENTRIES:
        data = data[-MAX_ENTRIES:]

    _save_web_vitals(data)
    return {"status": "ok"}


@router.get("/api/metrics/web-vitals")
async def get_web_vitals_aggregated() -> dict[str, Any]:
    data = _load_web_vitals()
    grouped: dict[tuple[str, str], list[float]] = {}
    for entry in data:
        route = entry.get("route", "unknown")
        name = entry.get("name", "unknown")
        value = entry.get("value")
        if value is not None and isinstance(value, (int, float)):
            grouped.setdefault((route, name), []).append(float(value))

    results: dict[str, dict[str, dict[str, float | None]]] = {}
    for (route, name), values in grouped.items():
        results.setdefault(route, {})[name] = {
            "p50": _compute_percentile(values, 50),
            "p75": _compute_percentile(values, 75),
            "p95": _compute_percentile(values, 95),
            "count": len(values),
        }

    return {
        "routes": results,
        "sample_rate": SAMPLE_RATE,
        "total_entries": len(data),
    }
