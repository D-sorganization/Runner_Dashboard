#!/usr/bin/env python3
"""Run Barb routing evaluation, measure accuracy, and record results for Board display (SC-C7, Issue #1340).

Usage:
    python scripts/eval_barb_routing.py [--deterministic] [--record] [--json]
    python scripts/eval_barb_routing.py --candidates
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Add backend to path for local script execution
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from staff.routing_eval import (  # noqa: E402
    DEFAULT_CASES_PATH,
    evaluate_deterministic,
    evaluate_full,
    extract_candidate_cases_from_feedback,
    load_eval_cases,
    save_eval_result,
)

log = logging.getLogger("eval_barb_routing")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Barb routing accuracy and record nightly metrics for the Board."
    )
    parser.add_argument(
        "--cases-file",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to evaluation cases JSON file",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Run only deterministic pre-router evaluation",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Persist evaluation result to config dir for Board display",
    )
    parser.add_argument(
        "--record-path",
        type=Path,
        default=None,
        help="Explicit file path to record evaluation result",
    )
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="Extract candidate evaluation cases from SC-C2 override feedback",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Minimum required accuracy (0.0 to 1.0, default 1.0: every case must pass)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output evaluation results as JSON",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Display detailed failure list",
    )
    return parser


def format_text_report(data: dict[str, Any], verbose: bool = False) -> str:
    lines = [
        "============================================================",
        f" Barb Routing Evaluation Report ({data.get('mode', 'unknown').upper()})",
        f" Evaluated at: {data.get('evaluated_at')}",
        "============================================================",
        f" Total cases: {data.get('total')}",
        f" Passed:      {data.get('passed')}",
        f" Accuracy:    {data.get('accuracy', 0.0) * 100:.1f}%",
        "------------------------------------------------------------",
        " Category Breakdown:",
    ]
    for cat, stats in data.get("categories", {}).items():
        acc = stats.get("accuracy", 0.0) * 100
        lines.append(f"   {cat:<20} {stats.get('passed')}/{stats.get('total')} ({acc:.1f}%)")

    failures = data.get("failures", [])
    if failures and verbose:
        lines.append("------------------------------------------------------------")
        lines.append(" Failures:")
        for f in failures:
            lines.append(f"   [{f.get('id')}] {f.get('prompt')}")
            lines.append(f"       -> {f.get('reason')}")

    lines.append("============================================================")
    return "\n".join(lines)


def run_candidates_command(as_json: bool) -> int:
    try:
        candidates = extract_candidate_cases_from_feedback()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Error reading routing feedback: {exc}\n")
        return 1

    if as_json:
        print(json.dumps({"candidates": candidates, "count": len(candidates)}, indent=2))
    else:
        print(f"Extracted {len(candidates)} candidate case(s) from routing overrides:")
        for idx, cand in enumerate(candidates, 1):
            print(f'  {idx}. Prompt: "{cand["prompt"]}"')
            print(f"     Override role: {cand['expected_role']} (was: {cand['original_role']})")
            print(f"     Reason: {cand['reason']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.candidates:
        return run_candidates_command(args.json)

    try:
        cases = load_eval_cases(args.cases_file)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Failed to load evaluation cases: {exc}\n")
        return 1

    if args.deterministic:
        selected_cases = [c for c in cases if c.deterministic]
        result = evaluate_deterministic(selected_cases)
    else:
        result = evaluate_full(cases)

    result_dict = result.to_dict()

    if args.record or args.record_path:
        save_eval_result(result, path=args.record_path)

    if args.json:
        print(json.dumps(result_dict, indent=2))
    else:
        print(format_text_report(result_dict, verbose=args.verbose))

    if result.accuracy < args.threshold:
        sys.stderr.write(f"Accuracy {result.accuracy:.2%} is below threshold {args.threshold:.2%}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
