"""Unit tests for backend/pr_inventory.py (issue #80)."""

from __future__ import annotations  # noqa: E402

import sys  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

UTC = timezone.utc  # noqa: UP017

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pr_inventory  # noqa: E402  # noqa: E402

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_pr(
    number: int = 1,
    title: str = "Test PR",
    author: str = "alice",
    draft: bool = False,
    labels: list[str] | None = None,
    reviewers: list[str] | None = None,
    body: str = "",
    created_at: str | None = None,
    head_ref: str = "fix/thing",
    mergeable_state: str = "clean",
) -> dict:
    if created_at is None:
        created_at = datetime.now(tz=UTC).isoformat()
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/org/repo/pull/{number}",
        "user": {"login": author},
        "draft": draft,
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "requested_reviewers": [{"login": r} for r in (reviewers or [])],
        "body": body,
        "created_at": created_at,
        "head": {"ref": head_ref},
        "mergeable_state": mergeable_state,
    }


# ─── _normalise_pr ────────────────────────────────────────────────────────────


class TestNormalisePr:
    def test_basic_fields(self) -> None:
        raw = _make_pr(number=42, title="Add feature", author="bob")
        result = pr_inventory._normalise_pr(raw, "org/repo")
        assert result["repository"] == "org/repo"
        assert result["number"] == 42
        assert result["title"] == "Add feature"
        assert result["author"] == "bob"
        assert result["draft"] is False

    def test_labels_extracted(self) -> None:
        raw = _make_pr(labels=["bug", "ci"])
        result = pr_inventory._normalise_pr(raw, "org/repo")
        assert set(result["labels"]) == {"bug", "ci"}

    def test_requested_reviewers(self) -> None:
        raw = _make_pr(reviewers=["alice", "bob"])
        result = pr_inventory._normalise_pr(raw, "org/repo")
        assert set(result["requested_reviewers"]) == {"alice", "bob"}

    def test_draft_flag(self) -> None:
        raw = _make_pr(draft=True)
        result = pr_inventory._normalise_pr(raw, "org/repo")
        assert result["draft"] is True

    def test_head_ref(self) -> None:
        raw = _make_pr(head_ref="feat/new-thing")
        result = pr_inventory._normalise_pr(raw, "org/repo")
        assert result["head_ref"] == "feat/new-thing"

    def test_mergeable_state(self) -> None:
        raw = _make_pr(mergeable_state="dirty")
        result = pr_inventory._normalise_pr(raw, "org/repo")
        assert result["mergeable_state"] == "dirty"


# ─── age_hours ────────────────────────────────────────────────────────────────


class TestAgeHours:
    def test_recent_pr(self) -> None:
        """A PR created 2 hours ago should report age ~2.0."""
        created = datetime.now(tz=UTC) - timedelta(hours=2)
        age = pr_inventory._age_hours(created.isoformat())
        assert 1.9 <= age <= 2.1

    def test_old_pr(self) -> None:
        created = datetime.now(tz=UTC) - timedelta(hours=48)
        age = pr_inventory._age_hours(created.isoformat())
        assert 47.9 <= age <= 48.1

    def test_invalid_date_returns_zero(self) -> None:
        assert pr_inventory._age_hours("not-a-date") == 0.0

    def test_z_suffix_handled(self) -> None:
        """ISO-8601 Z suffix should be accepted."""
        created = datetime.now(tz=UTC) - timedelta(hours=5)
        ts = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        age = pr_inventory._age_hours(ts)
        assert 4.9 <= age <= 5.1

    def test_result_is_rounded_to_one_decimal(self) -> None:
        created = datetime.now(tz=UTC) - timedelta(hours=1, minutes=6)
        age = pr_inventory._age_hours(created.isoformat())
        assert age == round(age, 1)


# ─── linked_issues ────────────────────────────────────────────────────────────


class TestParseLinkedIssues:
    def test_closes_keyword(self) -> None:
        body = "This PR closes #42."
        assert pr_inventory._parse_linked_issues(body) == [42]

    def test_fixes_keyword(self) -> None:
        body = "fixes #10"
        assert pr_inventory._parse_linked_issues(body) == [10]

    def test_resolves_keyword(self) -> None:
        body = "resolves #99"
        assert pr_inventory._parse_linked_issues(body) == [99]

    def test_case_insensitive(self) -> None:
        body = "CLOSES #1 FIXES #2 RESOLVES #3"
        assert pr_inventory._parse_linked_issues(body) == [1, 2, 3]

    def test_multiple_issues(self) -> None:
        body = "closes #5 and closes #10 and fixes #3"
        assert pr_inventory._parse_linked_issues(body) == [3, 5, 10]

    def test_no_keywords(self) -> None:
        assert pr_inventory._parse_linked_issues("Just a description.") == []

    def test_empty_body(self) -> None:
        assert pr_inventory._parse_linked_issues("") == []

    def test_none_body(self) -> None:
        assert pr_inventory._parse_linked_issues(None) == []

    def test_deduplicates(self) -> None:
        body = "closes #7 closes #7"
        assert pr_inventory._parse_linked_issues(body) == [7]


# ─── agent_claim parsing ──────────────────────────────────────────────────────


class TestParseAgentClaim:
    def test_claim_label(self) -> None:
        assert pr_inventory._parse_agent_claim(["claim:claude"]) == "claude"

    def test_no_claim(self) -> None:
        assert pr_inventory._parse_agent_claim(["bug", "ci"]) is None

    def test_first_claim_wins(self) -> None:
        result = pr_inventory._parse_agent_claim(["claim:jules", "claim:codex"])
        assert result in ("jules", "codex")

    def test_empty_labels(self) -> None:
        assert pr_inventory._parse_agent_claim([]) is None


# ─── per-repo error capture ───────────────────────────────────────────────────


class TestFetchRepoPrs:
    @pytest.mark.asyncio
    async def test_gh_failure_returns_error(self) -> None:
        with patch.object(pr_inventory, "_run_gh", new=AsyncMock(return_value=(1, "", "auth error"))):
            items, err = await pr_inventory.fetch_repo_prs("org/repo")
        assert items == []
        assert err is not None
        assert "org/repo" in err

    @pytest.mark.asyncio
    async def test_json_decode_error_returns_error(self) -> None:
        with patch.object(pr_inventory, "_run_gh", new=AsyncMock(return_value=(0, "not json", ""))):
            items, err = await pr_inventory.fetch_repo_prs("org/repo")
        assert items == []
        assert err is not None

    @pytest.mark.asyncio
    async def test_success_returns_normalised_items(self) -> None:
        import json

        raw = [_make_pr(1, "Fix bug", "alice")]
        with patch.object(
            pr_inventory,
            "_run_gh",
            new=AsyncMock(return_value=(0, json.dumps(raw), "")),
        ):
            items, err = await pr_inventory.fetch_repo_prs("org/repo")
        assert err is None
        assert len(items) == 1
        assert items[0]["number"] == 1
        assert items[0]["author"] == "alice"


# ─── fetch_all_prs error isolation ───────────────────────────────────────────


class TestFetchAllPrs:
    @pytest.mark.asyncio
    async def test_per_repo_error_captured(self) -> None:
        """An error for one repo should appear in errors[] but not fail others."""

        ok_prs = [_make_pr(1, "Ok PR", "bob")]

        async def fake_fetch(full_name: str) -> tuple[list, str | None]:
            if full_name == "org/bad":
                return [], "org/bad: gh exit 1: auth error"
            return [pr_inventory._normalise_pr(p, full_name) for p in ok_prs], None

        with patch.object(pr_inventory, "fetch_repo_prs", side_effect=fake_fetch):
            result = await pr_inventory.fetch_all_prs(
                ["org/good", "org/bad"],
                include_drafts=True,
            )

        assert result["errors"] != []
        assert any("org/bad" in e for e in result["errors"])
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_draft_filter(self) -> None:
        prs = [_make_pr(1, "Draft", draft=True), _make_pr(2, "Normal", draft=False)]

        async def fake_fetch(full_name: str) -> tuple[list, str | None]:
            return [pr_inventory._normalise_pr(p, full_name) for p in prs], None

        with patch.object(pr_inventory, "fetch_repo_prs", side_effect=fake_fetch):
            result = await pr_inventory.fetch_all_prs(["org/repo"], include_drafts=False)

        assert result["total"] == 1
        assert result["items"][0]["draft"] is False

    @pytest.mark.asyncio
    async def test_author_filter(self) -> None:
        prs = [
            _make_pr(1, "Alice PR", author="alice"),
            _make_pr(2, "Bob PR", author="bob"),
        ]

        async def fake_fetch(full_name: str) -> tuple[list, str | None]:
            return [pr_inventory._normalise_pr(p, full_name) for p in prs], None

        with patch.object(pr_inventory, "fetch_repo_prs", side_effect=fake_fetch):
            result = await pr_inventory.fetch_all_prs(["org/repo"], author="alice")

        assert result["total"] == 1
        assert result["items"][0]["author"] == "alice"

    @pytest.mark.asyncio
    async def test_limit_respected(self) -> None:
        prs = [_make_pr(i, f"PR {i}") for i in range(10)]

        async def fake_fetch(full_name: str) -> tuple[list, str | None]:
            return [pr_inventory._normalise_pr(p, full_name) for p in prs], None

        with patch.object(pr_inventory, "fetch_repo_prs", side_effect=fake_fetch):
            result = await pr_inventory.fetch_all_prs(["org/repo"], limit=3)

        assert result["total"] == 3
