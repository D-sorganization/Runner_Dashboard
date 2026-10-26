"""Hermetic tests for slot-health detection (#2898)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from slot_health import analyze_jobs, describe_slot, publish_slot_health_flags  # noqa: E402

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
_LABELS_LAPTOP = ["self-hosted", "Windows", "X64", "d-sorg-fleet", "d-sorg-fleet-portable"]
_LABELS_MINT = ["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-general"]


def _job(
    *,
    labels: list[str],
    job_name: str,
    conclusion: str,
    minutes_ago: int,
    log_excerpt: str = "",
) -> dict:
    ts = (_NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {
        "runner_id": hash(tuple(labels)) % 10000,
        "runner_labels": labels,
        "job_name": job_name,
        "conclusion": conclusion,
        "completed_at": ts,
        "log_excerpt": log_excerpt,
    }


def _arch_map_timeout_burst(labels: list[str], count: int) -> list[dict]:
    return [_job(labels=labels, job_name="architecture-map", conclusion="timed_out", minutes_ago=i) for i in range(count)]


def _healthy_fleet_jobs(n: int) -> list[dict]:
    jobs: list[dict] = []
    for i in range(n):
        jobs.append(
            _job(
                labels=["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-fast-io"],
                job_name="ci",
                conclusion="success",
                minutes_ago=i + 10,
            )
        )
    return jobs


def test_oct25_replay_flags_portable_arch_map_outlier() -> None:
    jobs = _healthy_fleet_jobs(30)
    jobs += [
        _job(
            labels=["self-hosted", "Linux", "X64", "d-sorg-fleet", "d-sorg-fleet-fast-io"],
            job_name="architecture-map",
            conclusion="success",
            minutes_ago=i + 40,
        )
        for i in range(45)
    ]
    jobs += _arch_map_timeout_burst(_LABELS_LAPTOP, 16)
    jobs += [
        _job(labels=_LABELS_MINT, job_name="backend", conclusion="failure", minutes_ago=1, log_excerpt="setup-python failed: Version 3.12.x is not available in python-versions manifest"),
    ] * 6
    report = analyze_jobs(jobs, now=_NOW, window_hours=24)
    reasons = {f["descriptor"]: f["reasons"] for f in report["flags"]}
    portable = describe_slot(_LABELS_LAPTOP)
    mint = describe_slot(_LABELS_MINT)
    assert "d-sorg-fleet-portable" in portable and "laptop" not in portable.lower()
    assert portable in reasons
    assert any("architecture-map" in r for r in reasons[portable])
    assert mint in reasons
    assert any("toolcache" in r or "setup-python" in r for r in reasons[mint])


def test_rate_rule_requires_three_x_fleet_and_five_samples() -> None:
    jobs = _healthy_fleet_jobs(20)
    jobs += _arch_map_timeout_burst(_LABELS_LAPTOP, 4)
    assert not analyze_jobs(jobs, now=_NOW)["flags"]


def test_publish_records_hub_and_barb_lane() -> None:
    flags = [{"descriptor": "Linux fleet runner (d-sorg-fleet-general)", "reasons": ["x"], "severity": "warning"}]
    events: list[dict] = []
    barb: list[str] = []
    class Store:
        def record(self, ev):  # noqa: ANN001
            events.append({"kind": ev.kind, "title": ev.title})
    publish_slot_health_flags(flags, event_store=Store(), barb_lane=lambda f: barb.append(f[0]["descriptor"]))
    assert events[0]["kind"] == "slot_health"
    assert barb == [flags[0]["descriptor"]]

