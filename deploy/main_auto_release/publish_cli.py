"""CLI for the main-ci auto-release workflow (gate verify, retention prune)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_DEPLOY = Path(__file__).resolve().parents[1]
if str(REPO_DEPLOY) not in sys.path:
    sys.path.insert(0, str(REPO_DEPLOY))

from node_auto_deploy.check_runs import PENDING_STATUSES, normalize_commit_sha  # noqa: E402

from main_auto_release.gate import (  # noqa: E402
    commit_passes_main_ci_gate,
    commit_passes_publisher_ci_gate,
    publisher_gate_failure_is_terminal,
)
from main_auto_release.naming import (  # noqa: E402
    AUTO_RELEASE_NOTES_HEADER,
    auto_release_tag_for_commit,
    auto_release_version_for_commit,
)
from main_auto_release.release_index import commit_has_release  # noqa: E402
from main_auto_release.retention import select_releases_to_delete  # noqa: E402


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_check_runs(payload: Any) -> tuple[list[dict[str, Any]] | None, str]:
    """Validate complete GitHub check-run pages before the strict gate sees them."""
    pages = payload if isinstance(payload, list) else [payload]
    if not pages or any(not isinstance(page, dict) for page in pages):
        return None, "expected one or more check-run page objects"

    flattened: list[dict[str, Any]] = []
    run_ids: set[int] = set()
    total_counts: set[int] = set()
    for page in pages:
        page_runs = page.get("check_runs")
        page_count = page.get("total_count")
        if (
            not isinstance(page_runs, list)
            or not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or page_count < 0
        ):
            return None, "page is missing a valid check_runs list or total_count"
        for run in page_runs:
            run_id = run.get("id") if isinstance(run, dict) else None
            name = run.get("name") if isinstance(run, dict) else None
            head_sha = run.get("head_sha") if isinstance(run, dict) else None
            status = run.get("status") if isinstance(run, dict) else None
            if (
                not isinstance(run_id, int)
                or isinstance(run_id, bool)
                or run_id <= 0
                or run_id in run_ids
                or not isinstance(head_sha, str)
                or normalize_commit_sha(head_sha) is None
                or not isinstance(name, str)
                or not name.strip()
                or not isinstance(status, str)
                or status not in PENDING_STATUSES | {"completed"}
            ):
                return None, "page contains an invalid or duplicated check-run identity"
            run_ids.add(run_id)
        flattened.extend(page_runs)
        total_counts.add(page_count)

    if len(total_counts) != 1:
        return None, "pagination pages disagree on total_count"
    expected_total = next(iter(total_counts))
    if len(flattened) != expected_total:
        return None, f"incomplete pagination: received {len(flattened)} of {expected_total} check runs"
    return flattened, ""


def cmd_verify_gate(args: argparse.Namespace) -> int:
    try:
        payload = _load_json(Path(args.check_runs_json))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"invalid check-runs payload: {type(exc).__name__}\n")
        return 3
    runs, error = _flatten_check_runs(payload)
    if runs is None:
        sys.stderr.write(f"invalid check-runs payload: {error}\n")
        return 3
    gate = commit_passes_publisher_ci_gate if args.publisher else commit_passes_main_ci_gate
    ok, detail = gate(runs, args.sha)
    sys.stdout.write(f"{detail}\n")
    if ok:
        return 0
    if args.publisher and publisher_gate_failure_is_terminal(runs, args.sha):
        sys.stderr.write("publisher gate cannot pass with these completed check runs\n")
        return 2
    return 1


def cmd_plan_tag(args: argparse.Namespace) -> int:
    tag = auto_release_tag_for_commit(args.sha, args.committer_date)
    version = auto_release_version_for_commit(args.sha, args.committer_date)
    sys.stdout.write(f"tag={tag}\n")
    sys.stdout.write(f"version={version}\n")
    sys.stdout.write(f"notes_header={AUTO_RELEASE_NOTES_HEADER}\n")
    return 0


def cmd_should_skip(args: argparse.Namespace) -> int:
    releases = _load_json(Path(args.releases_json))
    if not isinstance(releases, list):
        sys.stderr.write("releases payload must be a list\n")
        return 2
    if commit_has_release(releases, args.sha):
        sys.stdout.write(f"release already exists for {args.sha[:7]}\n")
        return 0
    sys.stdout.write(f"no release for {args.sha[:7]}\n")
    return 1


def cmd_prune_plan(args: argparse.Namespace) -> int:
    releases = _load_json(Path(args.releases_json))
    if not isinstance(releases, list):
        sys.stderr.write("releases payload must be a list\n")
        return 2
    protected = {s.strip().lower() for s in args.protected_sha.split(",") if s.strip()}
    to_delete = select_releases_to_delete(
        releases,
        keep_count=args.keep,
        protected_shas=protected,
    )
    for row in to_delete:
        sys.stdout.write(f"{row.get('tag_name', '')}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Main CI auto-release helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser(
        "verify-gate",
        help="Verify the strict deploy gate (0 pass, 1 wait, 2 terminal failure, 3 invalid/incomplete payload)",
    )
    verify.add_argument("--sha", required=True)
    verify.add_argument("--check-runs-json", required=True)
    verify.add_argument(
        "--publisher",
        action="store_true",
        help="Ignore this workflow's own in-flight check runs",
    )
    verify.set_defaults(func=cmd_verify_gate)

    plan = sub.add_parser("plan-tag", help="Print deterministic auto-release tag and version")
    plan.add_argument("--sha", required=True)
    plan.add_argument("--committer-date", required=True, help="GitHub commit.committer.date (UTC)")
    plan.set_defaults(func=cmd_plan_tag)

    skip = sub.add_parser("should-skip", help="Exit 0 when a release already targets the SHA")
    skip.add_argument("--sha", required=True)
    skip.add_argument("--releases-json", required=True)
    skip.set_defaults(func=cmd_should_skip)

    prune = sub.add_parser("prune-plan", help="Print auto-release tags eligible for deletion")
    prune.add_argument("--releases-json", required=True)
    prune.add_argument("--keep", type=int, default=30)
    prune.add_argument("--protected-sha", default="", help="Comma-separated full SHAs to never delete")
    prune.set_defaults(func=cmd_prune_plan)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
