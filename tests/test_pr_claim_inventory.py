"""Unit tests for read-only open PR vs claim inventory (#2905)."""

from __future__ import annotations

import datetime as _dt_mod
import io
import json
from datetime import datetime

import pytest

from scripts.pr_claim_inventory import (
    ReadOnlyViolation,
    build_inventory,
    classify_pr,
    fingerprint_from_ci_payload,
    guard_read_only_github,
    main,
)

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

UTC = _dt_mod.timezone.utc  # noqa: UP017


def test_classify_claimed() -> None:
    pr = {
        "number": 3101,
        "title": "feat(andon): add claim inventory",
        "body": "Implements inventory report.\n\nCloses #2905\n",
        "headRefName": "agy/2905-pr-claim-inventory",
        "labels": [{"name": "tdd"}, {"name": "claim:antigravity"}],
        "updatedAt": "2026-10-28T20:00:00Z",
    }
    now = datetime(2026, 10, 28, 22, 30, 0, tzinfo=UTC)
    row = classify_pr(pr, now=now)
    assert row.number == 3101
    assert row.linked_issue == 2905
    assert row.claim_agent == "antigravity"
    assert row.classification == "claimed"
    assert row.stale_hours == 2.5


def test_classify_backfillable() -> None:
    pr = {
        "number": 3102,
        "title": "fix(ci): handle empty queue",
        "body": "Fixes #2892",
        "headRefName": "cursor/queue-fix",
        "labels": [{"name": "ci"}],
        "updatedAt": "2026-10-28T21:00:00Z",
    }
    row = classify_pr(pr, now=datetime(2026, 10, 28, 22, 0, 0, tzinfo=UTC))
    assert row.number == 3102
    assert row.linked_issue == 2892
    assert row.claim_agent is None
    assert row.classification == "backfillable"
    assert row.stale_hours == 1.0


def test_classify_duplicate_candidate_by_fingerprint() -> None:
    payload_a = {
        "repo": "Runner_Dashboard",
        "workflow": "CI Standard",
        "check": "lint",
        "file": "backend/server.py",
        "error": "Failed at 2026-10-28T12:00:00Z on commit deadbeef1234 line 42: E501",
    }
    payload_b = {
        "repo": "Runner_Dashboard",
        "workflow": "CI Standard",
        "check": "lint",
        "file": "backend/server.py",
        "error": "Failed at 2026-10-28T19:45:00Z on commit cafebabe9876 line 99: E501",
    }
    fp_a = fingerprint_from_ci_payload(payload_a)
    fp_b = fingerprint_from_ci_payload(payload_b)
    assert fp_a == fp_b

    pr_first = {
        "number": 3201,
        "title": "fix: lint error 1",
        "body": f"Refs #2900\nandon-fingerprint: {fp_a}",
        "labels": [{"name": "claim:claude"}],
    }
    pr_dup = {
        "number": 3202,
        "title": "fix: lint error 2",
        "body": f"Refs #2900\nandon-fingerprint: {fp_b}",
        "labels": [],
    }
    direct_dup = classify_pr(pr_dup, seen_fingerprints={fp_a})
    assert direct_dup.fingerprint == fp_b
    assert direct_dup.classification == "duplicate_candidate"

    rows = build_inventory([pr_first, pr_dup])
    assert [r.classification for r in rows] == ["claimed", "duplicate_candidate"]


def test_guard_read_only_github_blocks_post() -> None:
    allowed = guard_read_only_github(["gh", "pr", "list", "--state", "open", "--json", "number"])
    assert allowed[0] == "gh"

    with pytest.raises(ReadOnlyViolation):
        guard_read_only_github(["gh", "api", "repos/o/r/issues/1/comments", "-X", "POST"])

    with pytest.raises(ReadOnlyViolation):
        guard_read_only_github(["gh", "pr", "close", "123"])

    with pytest.raises(ReadOnlyViolation):
        guard_read_only_github(["gh", "issue", "edit", "123", "--add-label", "claim:codex"])

    with pytest.raises(ReadOnlyViolation):
        guard_read_only_github("gh api graphql -f query='mutation { closePullRequest(input:{}) { clientMutationId } }'")


def test_build_inventory_covers_all_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def _forbid_subprocess(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("inventory must not invoke subprocess or write API calls")

    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)

    prs = [
        {
            "number": 101,
            "title": "claimed pr",
            "body": "Closes #201",
            "labels": ["claim:claude"],
        },
        {
            "number": 102,
            "title": "backfillable pr",
            "body": "Refs #202",
            "labels": [],
        },
        {
            "number": 103,
            "title": "dup 1",
            "body": "andon-fingerprint: aabbccddeeff0011",
            "labels": [],
        },
        {
            "number": 104,
            "title": "dup 2",
            "body": "andon-fingerprint: aabbccddeeff0011",
            "labels": [],
        },
        {
            "number": 105,
            "title": "orphan pr",
            "body": "no issue link and no fingerprint",
            "labels": [],
        },
    ]
    rows = build_inventory(prs)
    assert len(rows) == len(prs)
    by_num = {r.number: r for r in rows}
    assert by_num[101].classification == "claimed"
    assert by_num[102].classification == "backfillable"
    assert by_num[103].classification == "duplicate_candidate"
    assert by_num[104].classification == "duplicate_candidate"
    assert by_num[105].classification == "orphan"

    stdin = io.StringIO(json.dumps(prs))
    stdout = io.StringIO()
    stderr = io.StringIO()
    rc = main(["--stdin-json"], stdin=stdin, stdout=stdout, stderr=stderr)
    assert rc == 0
    report = json.loads(stdout.getvalue())
    assert report["total"] == 5
    assert len(report["items"]) == 5

    bad_out = io.StringIO()
    bad_err = io.StringIO()
    assert main(["--stdin-json"], stdin=io.StringIO("not-json"), stdout=bad_out, stderr=bad_err) == 2
    assert "::error::" in bad_err.getvalue()
