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
    is_auto_release_tag,
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


def _load_compare(path: Path, expected_base: str, expected_head: str | None = None) -> tuple[str, str, str, str] | None:
    """Validate GitHub compare metadata and bind it to the requested commits."""
    try:
        payload = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    base = payload.get("base_commit")
    head = payload.get("head_commit")
    merge_base = payload.get("merge_base_commit")
    status = payload.get("status")
    if not isinstance(base, dict) or not isinstance(merge_base, dict):
        return None
    base_sha = normalize_commit_sha(str(base.get("sha", "")))
    merge_base_sha = normalize_commit_sha(str(merge_base.get("sha", "")))
    expected_base_sha = normalize_commit_sha(expected_base)
    expected_head_sha = normalize_commit_sha(expected_head) if expected_head is not None else None
    if not isinstance(status, str):
        return None
    explicit_head_sha: str | None = None
    if isinstance(head, dict):
        explicit_head_sha = normalize_commit_sha(str(head.get("sha", "")))
        if explicit_head_sha is None:
            return None
    elif head is not None:
        return None
    commits = payload.get("commits")
    total_commits = payload.get("total_commits")
    if (
        not isinstance(commits, list)
        or not isinstance(total_commits, int)
        or isinstance(total_commits, bool)
        or total_commits < 0
        or len(commits) > 250
    ):
        return None

    if status == "identical":
        if total_commits != 0 or commits or base_sha != merge_base_sha:
            return None
        inferred_head_sha = base_sha
    elif status == "behind":
        if total_commits != 0 or commits:
            return None
        inferred_head_sha = merge_base_sha
        if expected_head_sha is not None and inferred_head_sha != expected_head_sha:
            return None
    elif status in {"ahead", "diverged"}:
        commit_shas = [
            normalize_commit_sha(str(item.get("sha", ""))) if isinstance(item, dict) else None for item in commits
        ]
        if (
            total_commits == 0
            or len(commit_shas) != min(total_commits, 250)
            or not commit_shas
            or any(sha is None for sha in commit_shas)
        ):
            return None
        # The request does not paginate compare responses. GitHub documents
        # that its last returned commit remains the comparison's latest even
        # when the response is capped at 250 commits.
        inferred_head_sha = commit_shas[-1]
    else:
        return None

    head_sha = explicit_head_sha or inferred_head_sha
    if head_sha != inferred_head_sha:
        return None
    if (
        base_sha is None
        or head_sha is None
        or merge_base_sha is None
        or expected_base_sha is None
        or base_sha != expected_base_sha
        or (expected_head is not None and head_sha != expected_head_sha)
        or status not in {"ahead", "behind", "diverged", "identical"}
    ):
        return None
    return status, base_sha, head_sha, merge_base_sha


def cmd_verify_main_ancestor(args: argparse.Namespace) -> int:
    candidate = normalize_commit_sha(args.sha)
    if candidate is None:
        sys.stderr.write("candidate SHA must be a full commit id\n")
        return 3
    compare = _load_compare(Path(args.compare_json), candidate)
    if compare is None:
        sys.stderr.write("invalid or mismatched GitHub main compare response\n")
        return 3
    status, base_sha, head_sha, merge_base_sha = compare
    if status == "identical" and base_sha == head_sha == merge_base_sha == candidate:
        sys.stdout.write("release candidate is current main\n")
        return 0
    if status == "ahead" and merge_base_sha == candidate:
        sys.stdout.write(f"release candidate {candidate[:7]} remains an ancestor of main {head_sha[:7]}\n")
        return 0
    if status == "behind" and merge_base_sha == head_sha:
        sys.stdout.write(f"release candidate {candidate[:7]} is not yet in main history\n")
        return 1
    if status == "diverged":
        sys.stdout.write(f"release candidate {candidate[:7]} is not an ancestor of current main\n")
        return 1
    sys.stderr.write("inconsistent GitHub main compare response; refusing release\n")
    return 3


def cmd_latest_auto_release_sha(args: argparse.Namespace) -> int:
    try:
        releases = _load_json(Path(args.releases_json))
    except (OSError, json.JSONDecodeError):
        sys.stderr.write("invalid releases payload\n")
        return 3
    if not isinstance(releases, list):
        sys.stderr.write("releases payload must be a list\n")
        return 3
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not is_auto_release_tag(tag):
            continue
        sha = normalize_commit_sha(str(release.get("target_commitish") or ""))
        if sha is None:
            sys.stderr.write("latest auto-release does not identify a full commit SHA\n")
            return 3
        sys.stdout.write(sha + "\n")
        return 0
    return 0


def cmd_verify_release_order(args: argparse.Namespace) -> int:
    candidate = normalize_commit_sha(args.sha)
    released = normalize_commit_sha(args.released_sha)
    if candidate is None or released is None:
        sys.stderr.write("candidate and released SHAs must be full commit ids\n")
        return 3
    if candidate == released:
        sys.stdout.write("candidate already has an auto-release\n")
        return 0
    compare = _load_compare(Path(args.compare_json), candidate, released)
    if compare is None:
        sys.stderr.write("invalid or mismatched GitHub release-order compare response\n")
        return 3
    status, _, _, merge_base_sha = compare
    if status == "ahead" and merge_base_sha == candidate:
        sys.stdout.write(f"newer auto-release {released[:7]} already descends from candidate {candidate[:7]}\n")
        return 1
    if status == "behind" and merge_base_sha == released:
        sys.stdout.write(f"candidate {candidate[:7]} is newer than latest auto-release {released[:7]}\n")
        return 0
    if status == "identical" and merge_base_sha == candidate:
        sys.stdout.write("candidate already has an auto-release\n")
        return 0
    sys.stderr.write("cannot prove candidate release order relative to latest auto-release\n")
    return 3


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

    ancestor = sub.add_parser(
        "verify-main-ancestor",
        help="Require GitHub compare metadata to prove the candidate is on current main history",
    )
    ancestor.add_argument("--sha", required=True)
    ancestor.add_argument("--compare-json", required=True)
    ancestor.set_defaults(func=cmd_verify_main_ancestor)

    latest_release = sub.add_parser(
        "latest-auto-release-sha",
        help="Print the newest auto-release target SHA, or nothing when no auto-release exists",
    )
    latest_release.add_argument("--releases-json", required=True)
    latest_release.set_defaults(func=cmd_latest_auto_release_sha)

    release_order = sub.add_parser(
        "verify-release-order",
        help="Prevent publication behind the latest auto-release using GitHub compare metadata",
    )
    release_order.add_argument("--sha", required=True)
    release_order.add_argument("--released-sha", required=True)
    release_order.add_argument("--compare-json", required=True)
    release_order.set_defaults(func=cmd_verify_release_order)

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
