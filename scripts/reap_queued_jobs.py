#!/usr/bin/env python3
"""Reap queued jobs via Dashboard stale queue endpoints.

This script fetches stale queued runs from /api/queue/stale and purges them
via /api/queue/purge-stale.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request

# Configure logging according to standards
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("reap_queued_jobs")


def str_to_bool(val: str | bool) -> bool:
    if isinstance(val, bool):
        return val
    return val.lower() in ("true", "1", "yes")


def main() -> None:
    # 1. Kill Switch Check
    disabled = os.environ.get("QUEUED_JOB_REAPER_DISABLED")
    if disabled and disabled.lower() == "true":
        logger.info("QUEUED_JOB_REAPER_DISABLED is set to true. Exiting early.")
        # If running in GHA, still write a step summary indicating it is disabled
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            try:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write("## Queued Job Reaper\n\n")
                    f.write("- **Status:** Disabled\n")
                    f.write("- **Reason:** QUEUED_JOB_REAPER_DISABLED is set to true\n")
            except Exception as e:
                logger.error("Failed to write to GITHUB_STEP_SUMMARY: %s", e)
        sys.exit(0)

    # 2. Parse arguments
    parser = argparse.ArgumentParser(description="Reap queued jobs via dashboard APIs")
    parser.add_argument("--dry-run", type=str, default="true", help="Dry run mode (true/false)")
    parser.add_argument("--min-age-minutes", type=int, default=60, help="Minimum age in minutes")
    parser.add_argument("--reason-filter", type=str, default=None, help="Filter stale runs by reason")
    parser.add_argument("--max-cancel", type=str, default=None, help="Maximum number of runs to cancel")
    parser.add_argument(
        "--safe-to-cancel-only",
        type=str,
        default="false",
        help="Only cancel safe_to_cancel runs (true/false)",
    )
    parser.add_argument("--dashboard-url", type=str, default="http://127.0.0.1:8321", help="Dashboard URL")

    args = parser.parse_args()

    dry_run = str_to_bool(args.dry_run)
    safe_to_cancel_only = str_to_bool(args.safe_to_cancel_only)
    min_age_minutes = args.min_age_minutes
    dashboard_url = args.dashboard_url.rstrip("/")

    reason_filter = args.reason_filter
    if reason_filter is not None and not reason_filter.strip():
        reason_filter = None

    max_cancel = None
    if args.max_cancel is not None and args.max_cancel.strip():
        try:
            max_cancel = int(args.max_cancel)
        except ValueError:
            logger.error("Invalid max-cancel value: %s", args.max_cancel)
            sys.exit(1)

    logger.info(
        "Starting queued-job reaper: dry_run=%s, safe_to_cancel_only=%s, min_age=%s",
        dry_run,
        safe_to_cancel_only,
        min_age_minutes,
    )

    # 3. GET /api/queue/stale
    query_params = []
    query_params.append(f"min_age_minutes={min_age_minutes}")
    if reason_filter:
        query_params.append(f"reason={reason_filter}")
    if max_cancel is not None:
        query_params.append(f"max_cancel={max_cancel}")
    if safe_to_cancel_only:
        query_params.append("safe_to_cancel_only=true")

    stale_url = f"{dashboard_url}/api/queue/stale"
    if query_params:
        stale_url += "?" + "&".join(query_params)

    logger.info("Fetching stale runs from: %s", stale_url)

    req_stale = urllib.request.Request(
        stale_url, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    )

    try:
        with urllib.request.urlopen(req_stale, timeout=45) as response:
            stale_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        logger.error("Failed to fetch stale queue: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error fetching stale queue: %s", e)
        sys.exit(1)

    stale_count = stale_data.get("stale_count", 0)
    logger.info("Stale runs found: %d", stale_count)

    # 4. POST /api/queue/purge-stale
    purge_url = f"{dashboard_url}/api/queue/purge-stale"
    purge_body = {
        "min_age_minutes": min_age_minutes,
        "reason": reason_filter,
        "dry_run": dry_run,
        "max_cancel": max_cancel,
        "safe_to_cancel_only": safe_to_cancel_only,
    }
    logger.info("Triggering purge on: %s", purge_url)

    req_purge = urllib.request.Request(
        purge_url,
        data=json.dumps(purge_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req_purge, timeout=60) as response:
            purge_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        logger.error("Failed to purge stale queue: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error purging stale queue: %s", e)
        sys.exit(1)

    # 5. Process results
    cancelled_runs = purge_data.get("runs", [])
    cancelled_count = purge_data.get("cancelled_count", 0)
    errors = purge_data.get("errors", [])

    logger.info("Purge result: dry_run=%s, cancelled=%d, errors=%d", dry_run, cancelled_count, len(errors))

    # Calculate counts by reason
    reason_counts: dict[str, int] = {}
    for r in cancelled_runs:
        reason = r.get("reason", "unknown")
        # If dry run is false, count actually cancelled runs; otherwise count would-cancel runs
        if not dry_run:
            if r.get("cancelled"):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        else:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    # 6. Generate GitHub step summary if running in GitHub Actions
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        logger.info("Writing step summary to: %s", summary_path)
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## Queued Job Reaper Summary\n\n")
                f.write(f"- **Dry Run:** `{dry_run}`\n")
                f.write(f"- **Min Queue Age:** `{min_age_minutes}` minutes\n")
                f.write(f"- **Safe To Cancel Only:** `{safe_to_cancel_only}`\n")
                f.write(f"- **Total Stale Candidate Runs:** {stale_count}\n")
                if not dry_run:
                    f.write(f"- **Successfully Cancelled:** {cancelled_count}\n")
                    f.write(f"- **Errors:** {len(errors)}\n")
                else:
                    f.write(f"- **Would Cancel:** {len(cancelled_runs)}\n")
                f.write("\n")

                # Table of counts by reason
                f.write("### Counts by Reason\n\n")
                if reason_counts:
                    f.write("| Reason | Count |\n")
                    f.write("|---|---|\n")
                    for reason, count in sorted(reason_counts.items()):
                        f.write(f"| `{reason}` | {count} |\n")
                else:
                    f.write("*No runs processed.*\n")
                f.write("\n")

                # Table of target runs
                f.write("### Target Runs Detail\n\n")
                if cancelled_runs:
                    f.write("| Repo | Run Link | Workflow | Branch | Reason | Safe to Cancel | Action/Status |\n")
                    f.write("|---|---|---|---|---|---|---|\n")
                    for r in cancelled_runs:
                        repo_name = r.get("repo", "")
                        run_id = r.get("run_id", 0)
                        workflow_name = r.get("workflow", "")
                        branch_name = r.get("branch", "")
                        reason_name = r.get("reason", "")
                        safe_val = r.get("safe_to_cancel", True)
                        run_url = r.get("url", "")

                        # Status display
                        if dry_run:
                            status_text = "Would Cancel" if safe_val or not safe_to_cancel_only else "Would Skip"
                        else:
                            if r.get("cancelled"):
                                status_text = "Cancelled"
                            else:
                                status_text = f"Failed: {r.get('cancel_error', 'unknown error')}"

                        f.write(
                            f"| {repo_name} | [{run_id}]({run_url}) | {workflow_name} | "
                            f"`{branch_name}` | `{reason_name}` | `{safe_val}` | {status_text} |\n"
                        )
                else:
                    f.write("*No target runs processed.*\n")
                f.write("\n")

                if errors:
                    f.write("### Execution Errors\n\n")
                    for err in errors:
                        f.write(f"- {err}\n")
                    f.write("\n")

        except Exception as e:
            logger.error("Failed to append to GITHUB_STEP_SUMMARY: %s", e)


if __name__ == "__main__":
    main()
