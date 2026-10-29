"""Sanitized issue migration tool (#3085, public-repo slice 7, epic #3077).

Plans which open issues on the source repo are safe to recreate on the fresh
public repo: an issue is skipped, never migrated, if its title or body trips
one of slice 2's publish-guard content patterns (``publish_guard.py``,
reused here for DRY instead of a second pattern list). Old pull requests are
refused outright -- they are closed or frozen, never migrated.

Dry-run by default: computes and prints counts only (migrate/skip), never
issue text. ``--apply`` writes the same counts plus the migratable issue
numbers to a private report path outside the repo (reusing slice 3's
``scan_history.validate_report_path``/``write_private_report``); it never
makes a GitHub write itself -- actually creating the issues on the target
repo is a separate owner-run step, same as slice 5's bootstrap tool.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _publish_guard() -> Any:  # local import: keeps this module importable standalone
    sys.path.insert(0, str(ROOT / "scripts"))
    import publish_guard  # noqa: PLC0415

    return publish_guard


def _scan_history() -> Any:
    sys.path.insert(0, str(ROOT / "scripts"))
    import scan_history  # noqa: PLC0415

    return scan_history


@dataclass(frozen=True)
class IssueDecision:
    number: int
    migrate: bool
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationPlan:
    decisions: tuple[IssueDecision, ...]

    @property
    def migrate_count(self) -> int:
        return sum(1 for d in self.decisions if d.migrate)

    @property
    def skip_count(self) -> int:
        return sum(1 for d in self.decisions if not d.migrate)

    def to_summary(self) -> dict[str, int]:
        return {"migrate_count": self.migrate_count, "skip_count": self.skip_count}


def evaluate_issue(issue: dict[str, Any], rules: Any) -> IssueDecision:
    """Flag pattern categories only; never surface the matched text itself."""
    text = f"{issue.get('title', '')}\n{issue.get('body', '')}"
    categories = tuple(sorted({category for category, pattern in rules.content.items() if pattern.search(text)}))
    return IssueDecision(number=issue["number"], migrate=not categories, categories=categories)


def plan_migration(issues: list[dict[str, Any]], rules: Any) -> MigrationPlan:
    if any("pull_request" in issue for issue in issues):
        raise ValueError("migration tool refuses pull requests; old PRs are closed or frozen, never migrated")
    return MigrationPlan(decisions=tuple(evaluate_issue(issue, rules) for issue in issues))


def to_apply_payload(plan: MigrationPlan) -> dict[str, Any]:
    payload: dict[str, Any] = dict(plan.to_summary())
    payload["migrate_numbers"] = sorted(d.number for d in plan.decisions if d.migrate)
    return payload


def fetch_open_issues(repo: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--state", "open", "--limit", "500", "--json", "number,title,body"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="D-sorganization/Runner_Dashboard", help="source repo")
    ap.add_argument("--fixtures", type=Path, help="JSON file of issue dicts, for testing without gh")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write a migration plan (issue numbers + counts only); never calls GitHub",
    )
    ap.add_argument("--out", type=Path, help="plan output path, required with --apply; must be outside the repo")
    args = ap.parse_args(argv)

    guard = _publish_guard()
    rules = guard.load_rules()
    issues = json.loads(args.fixtures.read_text(encoding="utf-8")) if args.fixtures else fetch_open_issues(args.repo)
    plan = plan_migration(issues, rules)

    if args.apply:
        if not args.out:
            ap.error("--apply requires --out")
        history = _scan_history()
        report_path = history.validate_report_path(args.out, ROOT)
        history.write_private_report(report_path, json.dumps(to_apply_payload(plan), indent=2, sort_keys=True))
        print(f"plan written to {report_path}; creating issues on the target repo remains a separate owner-run step")
        return 0

    summary = plan.to_summary()
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"migration dry-run: {summary['migrate_count']} to migrate, {summary['skip_count']} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
