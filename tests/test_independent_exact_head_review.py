"""Unit tests for independent exact-head review gating (#4315, #4323)."""

from __future__ import annotations

from shared_scripts.independent_exact_head_review import (
    INDEPENDENT_REVIEW_MARKER,
    RUNNER_DASHBOARD_BOT_LOGIN,
    head_sha_matches,
    independent_review_gate_active,
    independent_review_hold_reason,
    latest_independent_verdict_at_head,
    models_are_independent,
    parse_independent_review_comment,
    parse_issue_comments,
)

_AUTHOR = "agy-desk-001"
_REVIEWER = "antigravity-Brick-20261107-brick001"
_HEAD = "cccccccccccccccccccccccccccccccccccccccc"
_OLD_HEAD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_AGENT_LABELS = ("agent:cursor",)


def _independent_comment(
    verdict: str,
    sha: str,
    *,
    created_at: str = "2026-11-10T07:31:36Z",
    login: str = RUNNER_DASHBOARD_BOT_LOGIN,
) -> dict[str, str | dict[str, str]]:
    body = (
        f"### {INDEPENDENT_REVIEW_MARKER} (`seat` / `{_REVIEWER}`) — "
        f"Verdict: `{verdict} @{sha}`\n\n"
        f"- **Verdict:** **`{verdict} @{sha}`**\n"
    )
    return {"body": body, "created_at": created_at, "user": {"login": login}}


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


def test_missing_verdict_at_head_fails_closed_for_agent_prs() -> None:
    reasons = independent_review_hold_reason(
        [],
        head_sha=_HEAD,
        author_model=_AUTHOR,
        labels=_AGENT_LABELS,
    )
    assert reasons and "no independent exact-head verdict" in reasons[0]


def test_gate_inactive_without_agent_or_reviews() -> None:
    assert not independent_review_gate_active([], author_model="", labels=())
    assert independent_review_hold_reason([], head_sha=_HEAD, author_model="", labels=()) == []


def test_inactive_gate_skips_signal5_without_head_sha() -> None:
    """Skipping Signal 5 when the gate is inactive does not bypass agent-PR gating elsewhere."""
    assert independent_review_hold_reason([], head_sha="", author_model="", labels=()) == []


def test_active_gate_holds_when_head_sha_unknown() -> None:
    reasons = independent_review_hold_reason(
        [],
        head_sha="",
        author_model=_AUTHOR,
        labels=_AGENT_LABELS,
    )
    assert reasons and "head SHA is unknown" in reasons[0]


def test_active_gate_blocks_when_only_untrusted_verdict_comments() -> None:
    """Untrusted fleet-bot verdict comments do not satisfy an active gate (#4323)."""
    reviews = parse_issue_comments([_independent_comment("PASS", _HEAD, login="d-sorggeneralagent[bot]")])
    assert reviews == []
    reasons = independent_review_hold_reason(
        reviews,
        head_sha=_HEAD,
        author_model=_AUTHOR,
        labels=_AGENT_LABELS,
    )
    assert reasons and "no independent exact-head verdict" in reasons[0]


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
    assert reasons and "no independent exact-head verdict" in reasons[0]


def test_head_sha_matches_accepts_short_prefix() -> None:
    assert head_sha_matches("ccccccc", _HEAD)


def test_guard_ignores_generalagent_verdict_comments() -> None:
    row = _independent_comment("PASS", _HEAD, login="d-sorggeneralagent[bot]")
    assert parse_issue_comments([row]) == []


def test_guard_accepts_runner_dashboard_bot_author() -> None:
    row = _independent_comment("PASS", _HEAD, login=RUNNER_DASHBOARD_BOT_LOGIN)
    reviews = parse_issue_comments([row])
    assert len(reviews) == 1
    assert independent_review_hold_reason(reviews, head_sha=_HEAD, author_model=_AUTHOR) == []
