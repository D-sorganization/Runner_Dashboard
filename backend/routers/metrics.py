# ruff: noqa: B008
"""Lightweight Prometheus text exporter for fleet & staff telemetry (#2611)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Response
from identity import Principal, require_scope

router = APIRouter(tags=["metrics"])
_METRICS_READ = require_scope("staff.read")
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_LABEL_RE = re.compile(r"[^a-zA-Z0-9_.:-]")
_SNAPSHOT_OVERRIDE: Callable[[], dict[str, Any]] | None = None


def sanitize_metric_label(value: str) -> str:
    """Sanitize a Prometheus label value (DbC: non-empty ASCII identifier)."""
    cleaned = _LABEL_RE.sub("_", str(value or "").strip())[:64]
    return cleaned or "unknown"


def collect_metrics_snapshot() -> dict[str, Any]:
    """Read local cached fleet and staff state without remote HTTP fan-out."""
    if _SNAPSHOT_OVERRIDE is not None:
        return _SNAPSHOT_OVERRIDE()

    from cache_utils import cache_get  # noqa: PLC0415
    from fleet_status_cache import fleet_status_cache_key  # noqa: PLC0415
    from machine_registry import load_machine_registry  # noqa: PLC0415
    from staff import fleet as staff_fleet  # noqa: PLC0415
    from staff.classifier import format_attention_items  # noqa: PLC0415
    from staff.exclusions import active_exclusions  # noqa: PLC0415
    from staff.runner import get_runner  # noqa: PLC0415

    runner = get_runner()
    board = staff_fleet.local_board(runner)
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    recent = runner.store.list_runs(limit=200, since=since)

    status_counts = {"ok": 0, "failed": 0, "blocked": 0}
    spend_by_provider: dict[str, float] = {}
    for run in recent:
        st = getattr(run, "status", "")
        if st in ("succeeded", "ok", "completed"):
            status_counts["ok"] += 1
        elif st in ("failed", "error", "timed_out"):
            status_counts["failed"] += 1
        elif st in ("blocked", "cancelled", "canceled"):
            status_counts["blocked"] += 1
        prov = sanitize_metric_label(getattr(run, "provider", "") or "local")
        cost = float(getattr(run, "cost_usd", 0.0) or 0.0)
        spend_by_provider[prov] = spend_by_provider.get(prov, 0.0) + cost

    if not spend_by_provider:
        spend_by_provider["total"] = float(board.get("spend_today_usd") or 0.0)

    cordoned = {str(ex.get("machine") or ex.get("node") or "") for ex in active_exclusions() if isinstance(ex, dict)}
    cached_fleet = (
        cache_get(fleet_status_cache_key(exclude_pools=True), 3600.0)
        or cache_get(fleet_status_cache_key(exclude_pools=False), 3600.0)
        or {}
    )
    nodes: dict[str, dict[str, Any]] = {}
    for machine in load_machine_registry().get("machines", []):
        name = str(machine.get("name") or machine.get("hostname") or "").strip()
        if name:
            nodes[name] = {
                "online": int(name == runner.machine),
                "cordon": int(name in cordoned),
                "disk_free_bytes": 0,
            }
    for key, payload in cached_fleet.items() if isinstance(cached_fleet, dict) else []:
        if key.startswith("_") or not isinstance(payload, dict):
            continue
        online = int(not payload.get("offline") and payload.get("status") != "offline")
        disk = payload.get("disk") or payload.get("system", {}).get("disk") or {}
        free_b = int(disk.get("free_bytes") or float(disk.get("free_gb", 0.0) or 0.0) * (1024**3))
        nodes[str(key)] = {
            "online": online,
            "cordon": int(str(key) in cordoned or bool(payload.get("cordoned"))),
            "disk_free_bytes": max(0, free_b),
        }

    liveness = board.get("liveness") or []
    liveness_alerts = [item for item in liveness if isinstance(item, dict) and item.get("state") in ("late", "dead")]
    return {
        "nodes": nodes,
        "in_flight": len(board.get("running", [])) + len(board.get("queued", [])),
        "runs_24h": status_counts,
        "spend_today_usd": spend_by_provider,
        "attention_items": len(format_attention_items(recent)),
        "liveness_alerts": len(liveness_alerts),
        "holds_active": len(staff_fleet.holds_snapshot()),
    }


def render_prometheus_text(snapshot: dict[str, Any]) -> str:
    """Render a telemetry snapshot into Prometheus 0.0.4 exposition text."""
    lines = [
        "# HELP rd_node_online Whether the fleet node is online (1) or offline (0).",
        "# TYPE rd_node_online gauge",
    ]
    nodes = snapshot.get("nodes") or {}
    for node, info in sorted(nodes.items()):
        lbl = sanitize_metric_label(node)
        lines.append(f'rd_node_online{{node="{lbl}"}} {int(info.get("online", 0))}')

    lines.extend(
        [
            "# HELP rd_node_cordon Whether the fleet node is cordoned/excluded (1) or active (0).",
            "# TYPE rd_node_cordon gauge",
        ]
    )
    for node, info in sorted(nodes.items()):
        lbl = sanitize_metric_label(node)
        lines.append(f'rd_node_cordon{{node="{lbl}"}} {int(info.get("cordon", 0))}')

    lines.extend(
        [
            "# HELP rd_node_disk_free_bytes Free disk space in bytes on the node runner volume.",
            "# TYPE rd_node_disk_free_bytes gauge",
        ]
    )
    for node, info in sorted(nodes.items()):
        lbl = sanitize_metric_label(node)
        lines.append(f'rd_node_disk_free_bytes{{node="{lbl}"}} {int(info.get("disk_free_bytes", 0))}')

    lines.extend(
        [
            "# HELP rd_staff_runs_in_flight Currently running or queued staff runs.",
            "# TYPE rd_staff_runs_in_flight gauge",
            f"rd_staff_runs_in_flight {int(snapshot.get('in_flight', 0))}",
            "# HELP rd_staff_runs_24h_total Staff runs in the last 24 hours by terminal status.",
            "# TYPE rd_staff_runs_24h_total counter",
        ]
    )
    runs_24h = snapshot.get("runs_24h") or {}
    for status in ("ok", "failed", "blocked"):
        lines.append(f'rd_staff_runs_24h_total{{status="{status}"}} {int(runs_24h.get(status, 0))}')

    lines.extend(
        [
            "# HELP rd_spend_today_usd Today's estimated spend in USD by provider.",
            "# TYPE rd_spend_today_usd gauge",
        ]
    )
    for prov, usd in sorted((snapshot.get("spend_today_usd") or {}).items()):
        lbl = sanitize_metric_label(prov)
        lines.append(f'rd_spend_today_usd{{provider="{lbl}"}} {float(usd):.4f}')

    lines.extend(
        [
            "# HELP rd_attention_items_total Active items in the staff attention queue.",
            "# TYPE rd_attention_items_total gauge",
            f"rd_attention_items_total {int(snapshot.get('attention_items', 0))}",
            "# HELP rd_liveness_alerts_total Active scheduled-role liveness alerts.",
            "# TYPE rd_liveness_alerts_total gauge",
            f"rd_liveness_alerts_total {int(snapshot.get('liveness_alerts', 0))}",
            "# HELP rd_holds_active_total Active fleet/staff holds.",
            "# TYPE rd_holds_active_total gauge",
            f"rd_holds_active_total {int(snapshot.get('holds_active', 0))}",
            "",
        ]
    )
    return "\n".join(lines)


@router.get("/api/metrics", include_in_schema=False)
@router.get("/api/v1/metrics", include_in_schema=False)
async def get_prometheus_fleet_metrics(
    _peer: Principal = Depends(_METRICS_READ),
) -> Response:
    """Return fleet and staff telemetry in Prometheus 0.0.4 text format."""
    body = render_prometheus_text(collect_metrics_snapshot())
    return Response(
        content=body,
        media_type=PROMETHEUS_CONTENT_TYPE,
        headers={"Cache-Control": "no-store"},
    )
