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

from main_auto_release.gate import commit_passes_main_ci_gate  # noqa: E402
from main_auto_release.naming import (  # noqa: E402
    AUTO_RELEASE_NOTES_HEADER,
    auto_release_tag_for_commit,
    auto_release_version_for_commit,
)
from main_auto_release.release_index import commit_has_release  # noqa: E402
from main_auto_release.retention import select_releases_to_delete  # noqa: E402
from node_auto_deploy.ci_gate import parse_check_runs_payload  # noqa: E402


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_verify_gate(args: argparse.Namespace) -> int:
    payload = _load_json(Path(args.check_runs_json))
    runs = parse_check_runs_payload(payload if isinstance(payload, dict) else {"check_runs": payload})
    ok, detail = commit_passes_main_ci_gate(runs, args.sha)
    print(detail)
    return 0 if ok else 1


def cmd_plan_tag(args: argparse.Namespace) -> int:
    tag = auto_release_tag_for_commit(args.sha, args.committer_date)
    version = auto_release_version_for_commit(args.sha, args.committer_date)
    print(f"tag={tag}")
    print(f"version={version}")
    print(f"notes_header={AUTO_RELEASE_NOTES_HEADER}")
    return 0


def cmd_should_skip(args: argparse.Namespace) -> int:
    releases = _load_json(Path(args.releases_json))
    if not isinstance(releases, list):
        print("releases payload must be a list", file=sys.stderr)
        return 2
    if commit_has_release(releases, args.sha):
        print(f"release already exists for {args.sha[:7]}")
        return 0
    print(f"no release for {args.sha[:7]}")
    return 1


def cmd_prune_plan(args: argparse.Namespace) -> int:
    releases = _load_json(Path(args.releases_json))
    if not isinstance(releases, list):
        print("releases payload must be a list", file=sys.stderr)
        return 2
    protected = {s.strip().lower() for s in args.protected_sha.split(",") if s.strip()}
    to_delete = select_releases_to_delete(
        releases,
        keep_count=args.keep,
        protected_shas=protected,
    )
    for row in to_delete:
        print(row.get("tag_name", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Main CI auto-release helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify-gate", help="Exit 0 when check runs pass the deploy CI gate")
    verify.add_argument("--sha", required=True)
    verify.add_argument("--check-runs-json", required=True)
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
