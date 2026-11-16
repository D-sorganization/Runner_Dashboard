"""CLI for resource-shaping trial measurement (#4312)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from deploy.resource_shaping_trial.config import DEFAULT_CONFIG_PATH, load_trial_config
from deploy.resource_shaping_trial.measure import (
    compare_windows,
    load_measurement_bundle,
    recommend_go_no_go,
    render_go_no_go_report,
    summarize_workflow_runs,
)


def _latency_samples(bundle: dict) -> list[float]:
    raw = bundle.get("interactive_latency_ms")
    if not isinstance(raw, list):
        return []
    out: list[float] = []
    for item in raw:
        if isinstance(item, (int, float)):
            out.append(float(item))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resource shaping trial go/no-go harness")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--baseline", type=Path, required=True, help="Baseline window JSON")
    parser.add_argument("--trial", type=Path, required=True, help="Trial window JSON")
    parser.add_argument("--report-out", type=Path, help="Write markdown report to this path")
    parser.add_argument("--json-out", type=Path, help="Write machine-readable summary JSON")
    args = parser.parse_args(argv)

    cfg = load_trial_config(args.config)
    baseline_bundle = load_measurement_bundle(args.baseline)
    trial_bundle = load_measurement_bundle(args.trial)

    baseline = summarize_workflow_runs(
        baseline_bundle["workflow_runs"],
        interactive_latency_ms=_latency_samples(baseline_bundle),
    )
    trial = summarize_workflow_runs(
        trial_bundle["workflow_runs"],
        interactive_latency_ms=_latency_samples(trial_bundle),
    )
    verdict = compare_windows(baseline, trial, cfg["measurement"])
    decision = recommend_go_no_go(verdict)
    host_label = str(cfg.get("host_label") or "")

    report = render_go_no_go_report(
        baseline=baseline,
        trial=trial,
        verdict=verdict,
        decision=decision,
        host_label=host_label,
    )

    if args.report_out:
        args.report_out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(f"{report}\n")

    if args.json_out:
        payload = {
            "decision": decision,
            "baseline": baseline.__dict__,
            "trial": trial.__dict__,
            "verdict": {
                "regression": verdict.regression,
                "reasons": verdict.reasons,
                "failure_rate_delta_pp": verdict.failure_rate_delta_pp,
                "timeout_rate_delta_pp": verdict.timeout_rate_delta_pp,
                "duration_p95_increase_pct": verdict.duration_p95_increase_pct,
                "latency_p95_increase_pct": verdict.latency_p95_increase_pct,
            },
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return 1 if decision == "no-go" else 0


if __name__ == "__main__":
    raise SystemExit(main())
