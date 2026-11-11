"""Unit tests for independent exact-head review gating (#4315)."""

from __future__ import annotations

from shared_scripts.independent_exact_head_review import (
    INDEPENDENT_REVIEW_MARKER,
    head_sha_matches,
    independent_review_hold_reason,
    latest_independent_verdict_at_head,
    models_are_independent,
    parse_independent_review_comment,
    parse_issue_comments,
)

_AUTHOR = "agy-desk-001"
_REVIEWER = "antigravity-Brick-20261107-brick001"
_HEAD = "abc123def456abc123def456abc123def456abcd"
_OLD_HEAD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _independent_comment(verdict: str, sha: str, *, created_at: str = "2026-11-10T07:31:36Z") -> dict[str, str]:
    body = (
        f"### {INDEPENDENT_REVIEW_MARKER} (`seat` / `{_REVIEWER}`) — "
        f"Verdict: `{verdict} @{sha}`\n\n"
        f"- **Verdict:** **`{verdict} @{sha}`**\n"
    )
    return {"body": body, "created_at": created_at}


def test_fix_needed_on_head_blocks() -> None:
    reviews = parse_issue_comments([_independent_comment("FIX-NEEDED", _HEAD)])
    reasons = independent_review_hold_reason(
        reviews,
        head_sha=_HEAD,
        author_model=_AUTHOR,
    )
    assert reasons and "FIX-NEEDED" in reasons[0]


def test_stale_fix_needed_does_not_block_when_pass_on_new_head() -> None:
    reviews = parse_issue_comments(
        [
            _independent_comment("FIX-NEEDED", _OLD_HEAD, created_at="2026-11-10T07:00:00Z"),
            _independent_comment("PASS", _HEAD, created_at="2026-11-10T08:00:00Z"),
        ]
    )
    reasons = independent_review_hold_reason(reviews, head_sha=_HEAD, author_model=_AUTHOR)
    assert reasons == []
    verdict, _ = latest_independent_verdict_at_head(reviews, head_sha=_HEAD, author_model=_AUTHOR)
    assert verdict == "PASS"


def test_pass_at_head_allows_merge() -> None:
    reviews = parse_issue_comments([_independent_comment("PASS", _HEAD)])
    assert independent_review_hold_reason(reviews, head_sha=_HEAD, author_model=_AUTHOR) == []


def test_same_model_review_is_never_accepted() -> None:
    body = (
        f"### {INDEPENDENT_REVIEW_MARKER} (`x` / `{_AUTHOR}`) — Verdict: `FIX-NEEDED @{_HEAD}`"
    )
    parsed = parse_independent_review_comment(body)
    assert parsed is not None
    assert not models_are_independent(_AUTHOR, parsed.reviewer_model)
    reasons = independent_review_hold_reason(
        [parsed],
        head_sha=_HEAD,
        author_model=_AUTHOR,
    )
    assert reasons == []


def test_head_sha_matches_accepts_short_prefix() -> None:
    assert head_sha_matches("915e13f", _HEAD)
