"""Per-host EWMA trend baselines and disk-fill projection (#3934 S1).

Clean-room: built from public docs/behavior descriptions only; no source read
or copied.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

_DEFAULT_ALPHA = 0.3
_DEFAULT_Z_THRESHOLD = 3.0
_MIN_SAMPLES_FOR_ANOMALY = 5
_DEFAULT_THRESHOLD_HOURS = 24.0


@dataclass(frozen=True, slots=True)
class Baseline:
    """Rolling EWMA mean and deviation for one (host, metric) pair."""

    host: str
    metric: str
    mean: float
    deviation: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class Sample:
    """A single observed value for a metric at a point in time."""

    timestamp: datetime
    value: float


@dataclass(frozen=True, slots=True)
class DiskProjection:
    """Linear time-to-full projection for a host's disk-free trend."""

    host: str
    hours_to_full: float | None
    alert: bool


def _require_finite(value: float) -> None:
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"value must be finite, got {value!r}")


def update_baseline(
    baseline: Baseline | None,
    *,
    host: str,
    metric: str,
    value: float,
    alpha: float = _DEFAULT_ALPHA,
) -> Baseline:
    """Fold ``value`` into ``baseline`` using an EWMA mean and deviation.

    Passing ``baseline=None`` seeds a new baseline from the first sample.
    """
    _require_finite(value)
    if not 0.0 < alpha <= 1.0:
        raise ValueError(f"alpha must be in (0, 1], got {alpha!r}")

    if baseline is None:
        return Baseline(host=host, metric=metric, mean=value, deviation=0.0, sample_count=1)

    if baseline.host != host:
        raise ValueError(f"host mismatch: baseline is for {baseline.host!r}, got {host!r}")
    if baseline.metric != metric:
        raise ValueError(f"metric mismatch: baseline is for {baseline.metric!r}, got {metric!r}")

    delta = value - baseline.mean
    new_mean = baseline.mean + alpha * delta
    # EWMA of the absolute deviation from the (updated) mean.
    new_deviation = (1 - alpha) * baseline.deviation + alpha * abs(delta)

    return Baseline(
        host=host,
        metric=metric,
        mean=new_mean,
        deviation=new_deviation,
        sample_count=baseline.sample_count + 1,
    )


def is_anomalous(
    baseline: Baseline,
    value: float,
    *,
    z_threshold: float = _DEFAULT_Z_THRESHOLD,
    min_samples: int = _MIN_SAMPLES_FOR_ANOMALY,
) -> bool:
    """Return True when ``value`` is far enough from ``baseline`` to flag.

    A baseline with fewer than ``min_samples`` observations is not yet
    trustworthy, so it never flags regardless of how extreme the value is.
    """
    if baseline.sample_count < min_samples:
        return False
    if baseline.deviation == 0.0:
        return value != baseline.mean
    z_score = abs(value - baseline.mean) / baseline.deviation
    return z_score > z_threshold


def project_disk_fill(
    host: str,
    samples: Sequence[Sample],
    *,
    threshold_hours: float = _DEFAULT_THRESHOLD_HOURS,
) -> DiskProjection:
    """Project hours-to-full for a host from a disk-free sample history.

    Fits a simple linear trend (least squares) over ``samples``. When the
    trend is flat or growing, there is no time-to-full and no alert. When it
    is shrinking, projects how many hours remain until it reaches zero and
    alerts when that is under ``threshold_hours``.
    """
    if len(samples) < 2:
        raise ValueError("project_disk_fill requires at least two samples")

    ordered = sorted(samples, key=lambda sample: sample.timestamp)
    t0 = ordered[0].timestamp
    xs = [(sample.timestamp - t0).total_seconds() for sample in ordered]
    ys = [sample.value for sample in ordered]

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)

    if denominator == 0.0:
        return DiskProjection(host=host, hours_to_full=None, alert=False)

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator

    if slope >= 0:
        return DiskProjection(host=host, hours_to_full=None, alert=False)

    latest_free = ys[-1]
    seconds_to_full = latest_free / -slope
    hours_to_full = seconds_to_full / 3600.0
    alert = hours_to_full < threshold_hours

    return DiskProjection(host=host, hours_to_full=hours_to_full, alert=alert)
