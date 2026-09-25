#!/usr/bin/env python3
"""CLI script for Barb routing evaluation and regression checks (SC-C7, Issue #1340).

Evaluates the Barb two-stage router against the curated evaluation dataset,
ingests candidate cases from routing feedback overrides, and optionally
publishes the evaluation results to the Board.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure project root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from staff.conversations import get_conversation_store  # noqa: E402
from staff.router import BarbRouter  # noqa: E402

from tests.staff.routing_eval.engine import (  # noqa: E402
    post_eval_summary_to_board,
    run_evaluation,
)
from tests.staff.routing_eval.models import EvalSummary  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("eval_barb_routing")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run evaluation on Barb request routing and record accuracy metrics.",
    )
    parser.add_argument(
        "--post-board",
        action="store_true",
        help="Post evaluation summary as a Board Proposal work item.",
    )
    parser.add_argument(
        "--deterministic-only",
        action="store_true",
        help="Evaluate only the deterministic pre-router cases.",
    )
    parser.add_argument(
        "--include-feedback",
        action="store_true",
        default=True,
        help="Include candidate cases from user routing feedback overrides (default: True).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Minimum required accuracy threshold (default: 0.85).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON evaluation metrics report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    log.info("Starting Barb routing evaluation (deterministic_only=%s)...", args.deterministic_only)

    router = BarbRouter()
    store = get_conversation_store()

    summary: EvalSummary = run_evaluation(
        router=router,
        deterministic_only=args.deterministic_only,
        include_feedback_candidates=args.include_feedback,
        store=store,
    )

    # Print executive summary
    print("\n" + summary.to_markdown() + "\n")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
        log.info("Evaluation metrics written to %s", args.output)

    if args.post_board:
        work_item = post_eval_summary_to_board(summary, store=store)
        log.info("Published evaluation proposal to the Board (ID: %s)", work_item.id)

    if summary.accuracy < args.threshold:
        log.error(
            "Evaluation accuracy (%.1f%%) fell below threshold (%.1f%%)",
            summary.accuracy * 100.0,
            args.threshold * 100.0,
        )
        return 1

    log.info("Evaluation passed with %.1f%% accuracy!", summary.accuracy * 100.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
