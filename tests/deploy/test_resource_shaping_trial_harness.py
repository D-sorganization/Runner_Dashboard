"""Measurement harness for the per-runner resource-shaping trial (#4312)."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "resource-shaping-trial.json"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from deploy.resource_shaping_trial.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_trial_config,
    validate_trial_config,
)
from deploy.resource_shaping_trial.measure import (  # noqa: E402
    WindowSummary,
    compare_windows,
    load_measurement_bundle,
    recommend_go_no_go,
    summarize_workflow_runs,
)


def test_default_config_exists_and_is_disabled() -> None:
    assert CONFIG.is_file()
    cfg = load_trial_config(CONFIG)
    assert cfg["enabled"] is False
    assert cfg["systemd"]["cpu_weight"] == 20
    assert cfg["systemd"]["io_weight"] == 20
    assert cfg["systemd"]["nice"] == 10


def test_validate_rejects_hard_caps_in_systemd_values() -> None:
    cfg = load_trial_config(CONFIG)
    cfg["systemd"]["cpu_quota"] = "50%"
    with pytest.raises(ValueError, match="CPUQuota|excluded"):
        validate_trial_config(cfg)


def test_summarize_workflow_runs_counts_failures_and_timeouts() -> None:
    runs = [
        {
            "conclusion": "success",
            "run_started_at": "2026-11-01T10:00:00Z",
            "updated_at": "2026-11-01T10:12:00Z",
        },
        {
            "conclusion": "timed_out",
            "run_started_at": "2026-11-01T11:00:00Z",
            "updated_at": "2026-11-01T11:45:00Z",
        },
        {
            "conclusion": "failure",
            "run_started_at": "2026-11-01T12:00:00Z",
            "updated_at": "2026-11-01T12:05:00Z",
            "failure_class": "oom",
        },
    ]
    summary = summarize_workflow_runs(runs)
    assert summary.total == 3
    assert summary.timeouts == 1
    assert summary.failures == 1
    assert summary.oom_like == 1
    assert summary.median_duration_s == pytest.approx(720.0, rel=0.01)


def test_compare_windows_detects_regression() -> None:
    baseline = WindowSummary(
        total=100,
        successes=98,
        failures=1,
        timeouts=1,
        oom_like=0,
        median_duration_s=600.0,
        p95_duration_s=900.0,
        interactive_latency_p95_ms=120.0,
    )
    trial = WindowSummary(
        total=100,
        successes=94,
        failures=4,
        timeouts=2,
        oom_like=1,
        median_duration_s=720.0,
        p95_duration_s=1200.0,
        interactive_latency_p95_ms=250.0,
    )
    cfg = load_trial_config(CONFIG)
    verdict = compare_windows(baseline, trial, cfg["measurement"])
    assert verdict.regression is True
    assert recommend_go_no_go(verdict) == "no-go"
    assert "failure" in verdict.reasons[0].lower() or "timeout" in " ".join(verdict.reasons).lower()


def test_recommend_go_no_go_no_regression() -> None:
    baseline = WindowSummary(
        total=50,
        successes=50,
        failures=0,
        timeouts=0,
        oom_like=0,
        median_duration_s=500.0,
        p95_duration_s=800.0,
        interactive_latency_p95_ms=100.0,
    )
    trial = WindowSummary(
        total=50,
        successes=50,
        failures=0,
        timeouts=0,
        oom_like=0,
        median_duration_s=510.0,
        p95_duration_s=820.0,
        interactive_latency_p95_ms=105.0,
    )
    cfg = load_trial_config(CONFIG)
    verdict = compare_windows(baseline, trial, cfg["measurement"])
    decision = recommend_go_no_go(verdict)
    assert decision == "go"


def test_fixture_bundle_round_trip(tmp_path: Path) -> None:
    fixture_dir = REPO / "tests" / "deploy" / "fixtures" / "resource_shaping_trial"
    baseline = load_measurement_bundle(fixture_dir / "baseline.json")
    trial = load_measurement_bundle(fixture_dir / "trial.json")
    b = summarize_workflow_runs(baseline["workflow_runs"])
    t = summarize_workflow_runs(trial["workflow_runs"])
    assert b.total >= 1
    assert t.total >= 1


def test_default_config_path_points_at_repo_seed() -> None:
    assert DEFAULT_CONFIG_PATH.name == "resource-shaping-trial.json"


@pytest.mark.parametrize("baseline_total,trial_total", [(19, 50), (50, 19), (0, 0)])
def test_recommend_inconclusive_with_insufficient_samples(baseline_total: int, trial_total: int) -> None:
    summary = WindowSummary(50, 50, 0, 0, 0, 300.0, 600.0, 100.0)
    baseline = replace(summary, total=baseline_total, successes=baseline_total)
    trial = replace(summary, total=trial_total, successes=trial_total)
    verdict = compare_windows(baseline, trial, load_trial_config(CONFIG)["measurement"])
    assert recommend_go_no_go(verdict) == "inconclusive"


@pytest.mark.parametrize(
    "key,value",
    [
        ("cpu_weight", 10001),
        ("io_weight", True),
        ("nice", 20),
        ("slice_name", "../outside.slice"),
        ("memory_high", "1750M\nMemoryMax=1M"),
        ("memory_high_enabled", "false"),
    ],
)
def test_validate_rejects_invalid_systemd_settings(key: str, value: object) -> None:
    cfg = load_trial_config(CONFIG)
    cfg["systemd"][key] = value
    with pytest.raises(ValueError, match=f"systemd.{key}"):
        validate_trial_config(cfg)
