"""A review whose publication fails is not a verdict (#4249, board ruling 2026-11-13).

The head stays unreviewed until the published review on that exact SHA has been read
back from GitHub. These tests drive the real finalizer against a fake GitHub API, so
they cover the post, the read-back and what the shepherd and the review dedupe see.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from coordination.merge_shepherd_exact_head import collect_review_passes_from_store, shepherd_arm_eligibility
from staff.independent_exact_head_verdict import INDEPENDENT_REVIEW_MARKER
from staff.review import _already_reviewed, finalize_code_reviewer_run
from staff.staff_github_pr_review import RUNNER_DASHBOARD_BOT_LOGIN

pytestmark = pytest.mark.unit

_HEAD = "c" * 40
_OLD = "b" * 40
_PR = 42
_REPO = "Runner_Dashboard"
_ENDED = "2026-11-14T12:00:00+00:00"
_NOW = datetime(2026, 11, 14, 13, tzinfo=UTC)
_BOT = {"login": RUNNER_DASHBOARD_BOT_LOGIN}


class FakeGitHub:
    """The four endpoints the finalizer uses. Each knob models one way publication fails."""

    def __init__(self) -> None:
        self.reviews: list[dict[str, Any]] = []
        self.comments: dict[int, str] = {}
        self.review_post_status = 200
        self.review_post_is_recorded = True
        self.review_recorded_at = _HEAD
        self.comment_post_status = 201
        self.comment_is_readable = True
        self.comment_stored_body: str | None = None
        self.comment_get_failures = 0
        self.review_reads_that_lag = 0
        self.reviews_are_json = True
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> httpx.Response:
        self.calls.append(f"GET {url.split('?')[0].split('/repos/')[1]}")
        if url.endswith(f"/pulls/{_PR}"):
            return httpx.Response(200, json={"head": {"sha": _HEAD}})
        if f"/pulls/{_PR}/reviews" in url:
            if not self.reviews_are_json:
                return httpx.Response(200, text="<html>bad gateway</html>")
            rows = list(self.reviews)
            if self.review_reads_that_lag > 0 and any(row["id"] == 7 for row in rows):
                self.review_reads_that_lag -= 1
                rows = [row for row in rows if row["id"] != 7]
            return httpx.Response(200, json=rows)
        if "/issues/comments/" in url:
            comment_id = int(url.rsplit("/", 1)[1])
            if self.comment_get_failures > 0:
                self.comment_get_failures -= 1
                return httpx.Response(502, json={"message": "Bad Gateway"})
            if not self.comment_is_readable or comment_id not in self.comments:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"id": comment_id, "body": self.comments[comment_id], "user": _BOT})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, *, json: dict[str, Any], **_: Any) -> httpx.Response:
        self.calls.append(f"POST {url.split('/repos/')[1]}")
        if url.endswith(f"/pulls/{_PR}/reviews"):
            if self.review_post_status >= 400:
                return httpx.Response(self.review_post_status, json={"message": "Resource not accessible"})
            if self.review_post_is_recorded:
                row = {"id": 7, "state": "APPROVED", "commit_id": self.review_recorded_at, "user": _BOT}
                self.reviews.append(row)
            return httpx.Response(200, json={"id": 7})
        if url.endswith(f"/issues/{_PR}/comments"):
            if self.comment_post_status >= 400:
                return httpx.Response(self.comment_post_status, json={"message": "Resource not accessible"})
            body = self.comment_stored_body if self.comment_stored_body is not None else json["body"]
            self.comments[501] = body
            return httpx.Response(201, json={"id": 501})
        raise AssertionError(f"unexpected POST {url}")

    def put(self, url: str, **_: Any) -> httpx.Response:
        self.calls.append(f"PUT {url.split('/repos/')[1]}")
        review_id = int(url.split("/reviews/")[1].split("/")[0])
        for row in self.reviews:
            if row["id"] == review_id:
                row["state"] = "DISMISSED"
        return httpx.Response(200, json={"id": review_id, "state": "DISMISSED"})

    def delete(self, url: str, **_: Any) -> httpx.Response:
        self.calls.append(f"DELETE {url.split('/repos/')[1]}")
        self.comments.pop(int(url.rsplit("/", 1)[1]), None)
        return httpx.Response(204)

    def approved_at_head(self) -> list[int]:
        return [row["id"] for row in self.reviews if row["state"] == "APPROVED" and row["commit_id"] == _HEAD]

    def posted(self, suffix: str) -> int:
        return sum(call.startswith("POST") and call.endswith(suffix) for call in self.calls)


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    token = MagicMock(token="ghs_" + "A" * 40, expires_at=1_700_000_000.0)
    with (
        patch("staff.independent_exact_head_verdict.mint_runner_dashboard_verdict_token", return_value=token),
        patch("staff.staff_github_pr_review.mint_runner_dashboard_verdict_token", return_value=token),
        patch.object(httpx, "get", side_effect=fake.get),
        patch.object(httpx, "post", side_effect=fake.post),
        patch.object(httpx, "put", side_effect=fake.put),
        patch.object(httpx, "delete", side_effect=fake.delete),
        patch("time.sleep"),
    ):
        yield fake


def _finalize(result_line: str, *, pr_number: int | None = _PR) -> Any:
    return finalize_code_reviewer_run(
        status="succeeded",
        failure_class="",
        retryable=False,
        error="",
        remediation="",
        result_line=result_line,
        same_provider=False,
        repo=_REPO,
        reviewer_model="gpt-6.1-sol",
        expected_pr_number=pr_number,
    )


_ADVISORY_APPROVE = f"STAFF_RESULT: review approve #{_PR} @{_HEAD}"
_INDEPENDENT_PASS = f"STAFF_RESULT: independent_review PASS #{_PR} @{_HEAD}"
_INDEPENDENT_FIX = f"STAFF_RESULT: independent_review FIX-NEEDED #{_PR} @{_HEAD}"


class _RunStore:
    """What the run store holds after the runner records the finalizer's outcome."""

    def __init__(self, status: str, last_line: str) -> None:
        self._runs = [
            SimpleNamespace(
                repo=_REPO,
                role="code-reviewer",
                status=status,
                outcome="",
                last_line=last_line,
                ended_at=_ENDED,
                created_at=_ENDED,
                model="gpt-6.1-sol",
                retry_of="",
            )
        ]

    def list_runs(self, limit: int = 500) -> list[Any]:
        return list(self._runs)

    def list_runs_for_target(self, *, role: str, repo: str, target_ref: str) -> list[Any]:
        return list(self._runs)


def _arm_kind(store: _RunStore) -> tuple[str, str]:
    return shepherd_arm_eligibility(
        review_decision="APPROVED",
        file_paths=["backend/x.py"],
        additions=10,
        deletions=2,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
        pr_number=_PR,
        head_sha=_HEAD,
        review_passes=collect_review_passes_from_store(store, _REPO),
        now=_NOW,
    )


def test_a_403_on_the_review_post_leaves_the_head_unreviewed_and_unarmable(github: FakeGitHub) -> None:
    github.review_post_status = 403

    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "failed"
    assert "403" in out.error
    store = _RunStore(out.status, _ADVISORY_APPROVE)
    assert not _already_reviewed(store, _REPO, _PR), "a failed publication must leave a review obligation"
    assert _arm_kind(store) == ("no_approval", "missing_pass_at_head")


def test_a_review_post_that_github_did_not_record_is_not_a_verdict(github: FakeGitHub) -> None:
    github.review_post_is_recorded = False

    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "failed"
    assert out.failure_class == "provider_error"
    assert "read-back" in out.error
    assert _arm_kind(_RunStore(out.status, _ADVISORY_APPROVE))[0] == "no_approval"


def test_a_review_recorded_on_another_sha_is_not_a_verdict_for_this_head(github: FakeGitHub) -> None:
    github.review_recorded_at = _OLD

    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "failed"
    assert "read-back" in out.error


def test_a_review_read_back_on_the_exact_sha_is_accepted(github: FakeGitHub) -> None:
    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "succeeded"
    reviews = f"{_REPO}/pulls/{_PR}/reviews"
    assert [c for c in github.calls if c.endswith(reviews)][-2:] == [
        f"POST D-sorganization/{reviews}",
        f"GET D-sorganization/{reviews}",
    ]
    store = _RunStore(out.status, _ADVISORY_APPROVE)
    assert _already_reviewed(store, _REPO, _PR)
    assert _arm_kind(store)[0] == "reviewed"


def test_an_approval_already_published_on_this_sha_is_read_back_and_not_posted_again(github: FakeGitHub) -> None:
    github.reviews.append({"id": 3, "state": "APPROVED", "commit_id": _HEAD, "user": _BOT})
    github.review_post_status = 403  # a second post would fail; none may be attempted

    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "succeeded"
    assert github.posted("/reviews") == 0


def test_independent_pass_publishes_no_pass_comment_when_the_review_post_is_refused(github: FakeGitHub) -> None:
    github.review_post_status = 403

    out = _finalize(_INDEPENDENT_PASS)

    assert out.status == "failed"
    assert "403" in out.remediation
    assert github.posted("/comments") == 0, "a PASS comment must not be visible for a head whose review failed"


def test_independent_pass_is_accepted_after_both_read_backs(github: FakeGitHub) -> None:
    out = _finalize(_INDEPENDENT_PASS)

    assert out.status == "succeeded"
    assert github.posted("/reviews") == 1
    assert github.posted("/comments") == 1
    assert "GET D-sorganization/Runner_Dashboard/issues/comments/501" in github.calls
    assert INDEPENDENT_REVIEW_MARKER in github.comments[501]
    assert f"PASS @{_HEAD}" in github.comments[501]


def test_a_403_on_the_verdict_comment_fails_the_run(github: FakeGitHub) -> None:
    github.comment_post_status = 403

    out = _finalize(_INDEPENDENT_FIX)

    assert out.status == "failed"
    assert "403" in out.remediation


def test_a_verdict_comment_that_cannot_be_read_back_fails_the_run(github: FakeGitHub) -> None:
    github.comment_is_readable = False

    out = _finalize(_INDEPENDENT_FIX)

    assert out.status == "failed"
    assert "read-back" in out.error


def test_a_verdict_comment_read_back_without_this_sha_fails_the_run(github: FakeGitHub) -> None:
    github.comment_stored_body = f"### {INDEPENDENT_REVIEW_MARKER} — Verdict: `FIX-NEEDED @{_OLD}`"

    out = _finalize(_INDEPENDENT_FIX)

    assert out.status == "failed"
    assert "read-back" in out.error


def test_a_fix_needed_verdict_read_back_on_the_exact_sha_is_accepted(github: FakeGitHub) -> None:
    out = _finalize(_INDEPENDENT_FIX)

    assert out.status == "succeeded"
    assert github.posted("/reviews") == 0
    assert f"FIX-NEEDED @{_HEAD}" in github.comments[501]


def test_a_pass_whose_comment_is_refused_withdraws_the_approve_it_just_posted(github: FakeGitHub) -> None:
    github.comment_post_status = 403

    out = _finalize(_INDEPENDENT_PASS)

    assert out.status == "failed"
    assert github.approved_at_head() == [], "no half of a failed PASS may stay published"


def test_a_pass_whose_comment_cannot_be_read_back_leaves_nothing_published(github: FakeGitHub) -> None:
    github.comment_is_readable = False

    out = _finalize(_INDEPENDENT_PASS)

    assert out.status == "failed"
    assert github.approved_at_head() == []
    assert github.comments == {}


def test_a_failed_comment_does_not_withdraw_an_approval_published_by_an_earlier_run(github: FakeGitHub) -> None:
    github.reviews.append({"id": 3, "state": "APPROVED", "commit_id": _HEAD, "user": _BOT})
    github.comment_post_status = 403

    out = _finalize(_INDEPENDENT_PASS)

    assert out.status == "failed"
    assert github.approved_at_head() == [3]


def test_a_review_list_that_lags_behind_the_post_is_read_again(github: FakeGitHub) -> None:
    github.review_reads_that_lag = 1

    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "succeeded"
    assert github.posted("/reviews") == 1


def test_a_review_that_never_appears_fails_after_the_retries(github: FakeGitHub) -> None:
    github.review_reads_that_lag = 99

    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "failed"
    assert "read-back" in out.error


def test_one_failed_comment_read_is_retried(github: FakeGitHub) -> None:
    github.comment_get_failures = 1

    out = _finalize(_INDEPENDENT_FIX)

    assert out.status == "succeeded"
    assert 501 in github.comments


def test_a_review_list_that_is_not_json_fails_the_run_as_a_provider_error(github: FakeGitHub) -> None:
    github.reviews_are_json = False

    out = _finalize(_ADVISORY_APPROVE)

    assert out.status == "failed"
    assert out.failure_class == "provider_error"


def test_an_approve_line_on_a_run_without_a_pull_request_is_not_a_verdict(github: FakeGitHub) -> None:
    out = _finalize(_ADVISORY_APPROVE, pr_number=None)

    assert out.status == "failed"
    assert github.calls == []
    assert collect_review_passes_from_store(_RunStore(out.status, _ADVISORY_APPROVE), _REPO) == ()
