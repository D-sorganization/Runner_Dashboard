"""Tests for scripts/migrate_issues.py (#3085, public-repo slice 7, epic #3077).

Offline throughout: ``plan_migration`` takes injected issue dicts and the
real publish_guard content-pattern rules (slice 2, reused here for DRY) --
no network, no ``gh`` calls. Only categories, numbers and counts are ever
surfaced; matched text itself is never echoed, per the epic's "nothing
security-confidential" constraint.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import migrate_issues as migrate  # noqa: E402
import publish_guard  # noqa: E402

_RULES = publish_guard.load_rules()


# Per-issue evaluation


def test_evaluate_issue_migrates_clean_issue() -> None:
    decision = migrate.evaluate_issue({"number": 1, "title": "Fix flaky test", "body": "retry logic"}, _RULES)
    assert decision.migrate
    assert decision.categories == ()


@pytest.mark.parametrize(
    ("title", "body", "category"),
    [
        ("Node unreachable", "host.example.ts.net is down", "tailnet-name"),
        ("leak", "gh" + "p_" + "A" * 36, "github-token"),
        ("contact", "ping someone@gmail.com", "personal-email"),
        ("node.example.ts.net flapping", "", "tailnet-name"),  # title is scanned too
    ],
)
def test_evaluate_issue_skips_flagged_category(title: str, body: str, category: str) -> None:
    decision = migrate.evaluate_issue({"number": 2, "title": title, "body": body}, _RULES)
    assert not decision.migrate
    assert category in decision.categories


# Pull-request boundary (DbC precondition: old PRs are never migrated)


def test_plan_migration_refuses_pull_requests() -> None:
    try:
        migrate.plan_migration([{"number": 6, "title": "x", "body": "", "pull_request": {}}], _RULES)
    except ValueError as exc:
        assert "pull request" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# Dry-run plan: counts only, per the acceptance criterion


def test_plan_migration_reports_counts_only() -> None:
    issues = [
        {"number": 10, "title": "clean one", "body": "nothing sensitive"},
        {"number": 11, "title": "clean two", "body": "also fine"},
        {"number": 12, "title": "leaky", "body": "host.example.ts.net"},
    ]
    plan = migrate.plan_migration(issues, _RULES)
    assert plan.to_summary() == {"migrate_count": 2, "skip_count": 1}


def test_plan_migration_skips_every_flagged_issue() -> None:
    issues = [
        {"number": 20, "title": "a", "body": "ghp_" + "A" * 36},
        {"number": 21, "title": "b", "body": "xox" + "b-0000000000"},
        {"number": 22, "title": "c", "body": "clean"},
    ]
    plan = migrate.plan_migration(issues, _RULES)
    assert plan.skip_count == 2
    assert plan.migrate_count == 1


# Apply payload: numbers + counts only, never issue text


def test_apply_payload_carries_numbers_and_counts_only() -> None:
    issues = [
        {"number": 30, "title": "clean", "body": "fine"},
        {"number": 31, "title": "leaky", "body": "100.100.1.1"},
    ]
    plan = migrate.plan_migration(issues, _RULES)
    payload = migrate.to_apply_payload(plan)
    assert payload == {"migrate_count": 1, "skip_count": 1, "migrate_numbers": [30]}
    assert "fine" not in str(payload)
    assert "100.100.1.1" not in str(payload)
