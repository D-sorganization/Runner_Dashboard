from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from coordination import pr_age_rule as rule

# Relative to "now" so these fixtures never bit-rot into a flaky time bomb as
# the real clock advances past a hardcoded absolute date (#3953 CI-unblock).
_OLD_PR_CREATED_AT = (datetime.now(UTC) - timedelta(hours=360)).strftime("%Y-%m-%dT%H:%M:%SZ")
_FRESH_PR_CREATED_AT = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
_VERY_OLD_PR_CREATED_AT = (datetime.now(UTC) - timedelta(hours=800)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.unit
def test_resolve_pr_owner_from_branch_prefix() -> None:
    pr = {"head": {"ref": "cursor/flow-metrics-5904"}, "labels": []}
    owner = rule.resolve_pr_owner(pr, agent_ids=["cursor", "codex"])
    assert owner == "cursor"


@pytest.mark.unit
def test_list_aging_prs_for_agent_filters_by_age() -> None:
    def fetch(path: str) -> list[dict]:
        if path.endswith("/pulls?state=open&per_page=100"):
            return [
                {
                    "number": 1,
                    "html_url": "https://github.com/D-sorganization/Runner_Dashboard/pull/1",
                    "created_at": _OLD_PR_CREATED_AT,
                    "head": {"ref": "cursor/old-work"},
                    "labels": [],
                },
                {
                    "number": 2,
                    "html_url": "https://github.com/D-sorganization/Runner_Dashboard/pull/2",
                    "created_at": _FRESH_PR_CREATED_AT,
                    "head": {"ref": "cursor/fresh"},
                    "labels": [],
                },
            ]
        raise AssertionError(path)

    hits = rule.list_aging_prs_for_agent(
        "cursor",
        repos=["Runner_Dashboard"],
        fetch=fetch,
        rule_hours=72,
    )
    assert len(hits) == 1
    assert hits[0]["number"] == 1


@pytest.mark.unit
def test_claim_blocked_payload() -> None:
    block = rule.claim_blocked_by_aging_prs(
        "cursor",
        repos=["Runner_Dashboard"],
        fetch=lambda path: [
            {
                "number": 9,
                "html_url": "https://example/pull/9",
                "created_at": _VERY_OLD_PR_CREATED_AT,
                "head": {"ref": "cursor/stale"},
                "labels": [],
            }
        ]
        if "pulls" in path
        else [],
    )
    assert block is not None
    assert block["code"] == "aging_pr_blocks_claim"
    assert block["aging_prs"][0]["number"] == 9
