"""Stateful fleet health watchdog bridge (#3934 S2).

Wraps the S1 EWMA/disk-fill library (``disk_fill_trend``) in a per-process
registry that accumulates samples across polls, so each ``/api/metrics``
poll can report rolling anomaly flags and disk-fill-time projections
without the caller having to keep history itself.

Clean-room: built from public docs/behavior descriptions only; no source
read or copied.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

from disk_fill_trend import Baseline, Sample, is_anomalous, project_disk_fill, update_baseline

_DISK_METRIC = "disk_free_bytes"
_TRACKED_METRICS = (_DISK_METRIC, "in_flight", "queue_depth")
_DISK_HISTORY_LEN = 12


class HostTrendWatchdog:
    """Accumulates per-host EWMA baselines and disk-free sample history."""

    def __init__(self) -> None:
        self._baselines: dict[tuple[str, str], Baseline] = {}
        self._disk_history: dict[str, deque[Sample]] = {}

    def observe(self, nodes: dict[str, dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
        """Fold one poll's ``nodes`` snapshot in and return a row per host.

        Each row: ``host``, ``disk_free_bytes`` (``None`` if absent/non-numeric),
        ``anomaly`` (any tracked metric flagged anomalous this poll),
        ``hours_to_full`` (``None`` until a shrinking trend is established),
        ``disk_alert`` (hours_to_full under the S1 default threshold).
        """
        rows: list[dict[str, Any]] = []
        for host, metrics in nodes.items():
            anomaly = False
            for metric in _TRACKED_METRICS:
                value = metrics.get(metric)
                numeric = _as_finite_float(value)
                if numeric is None:
                    continue
                key = (host, metric)
                baseline = self._baselines.get(key)
                if baseline is not None and is_anomalous(baseline, numeric):
                    anomaly = True
                self._baselines[key] = update_baseline(baseline, host=host, metric=metric, value=numeric)

            disk_value = _as_finite_float(metrics.get(_DISK_METRIC))
            hours_to_full: float | None = None
            disk_alert = False
            if disk_value is not None:
                history = self._disk_history.setdefault(host, deque(maxlen=_DISK_HISTORY_LEN))
                history.append(Sample(timestamp=now, value=disk_value))
                if len(history) >= 2:
                    projection = project_disk_fill(host, list(history))
                    hours_to_full = projection.hours_to_full
                    disk_alert = projection.alert

            rows.append(
                {
                    "host": host,
                    "disk_free_bytes": disk_value,
                    "anomaly": anomaly,
                    "hours_to_full": hours_to_full,
                    "disk_alert": disk_alert,
                }
            )
        return rows


def _as_finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):  # noqa: PLR0124 - NaN check
        return None
    return numeric


_REGISTRY = HostTrendWatchdog()


def get_registry() -> HostTrendWatchdog:
    """The process-wide watchdog registry used by the ``/api/metrics`` poller."""
    return _REGISTRY


def render_watchdog_lines(rows: list[dict[str, Any]]) -> list[str]:
    """Prometheus 0.0.4 lines for a list of watchdog rows (labels: ``host``)."""
    if not rows:
        return []

    from routers.metrics import sanitize_metric_label  # noqa: PLC0415

    labeled = [(sanitize_metric_label(str(row["host"])), row) for row in rows]

    lines = [
        "# HELP rd_host_metric_anomaly Whether any tracked metric on the host is EWMA-anomalous this poll.",
        "# TYPE rd_host_metric_anomaly gauge",
    ]
    lines += [f'rd_host_metric_anomaly{{host="{lbl}"}} {int(row["anomaly"])}' for lbl, row in labeled]

    lines += [
        "# HELP rd_host_disk_fill_alert Whether the host's disk is projected to fill within the alert threshold.",
        "# TYPE rd_host_disk_fill_alert gauge",
    ]
    lines += [f'rd_host_disk_fill_alert{{host="{lbl}"}} {int(row["disk_alert"])}' for lbl, row in labeled]

    lines += [
        "# HELP rd_host_disk_fill_hours_to_full Hours until disk full, projected (absent when not shrinking).",
        "# TYPE rd_host_disk_fill_hours_to_full gauge",
    ]
    lines += [
        f'rd_host_disk_fill_hours_to_full{{host="{lbl}"}} {row["hours_to_full"]:.2f}'
        for lbl, row in labeled
        if row.get("hours_to_full") is not None
    ]
    return lines
