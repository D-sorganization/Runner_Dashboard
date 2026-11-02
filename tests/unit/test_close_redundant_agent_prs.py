"""Unit tests for redundant agent PR closer duplicate detection."""

from __future__ import annotations

from scripts.close_redundant_agent_prs import (
    PullView,
    duplicate_head_evidence,
    pr_closing_issue_numbers,
    run_closer,
)


def test_pr_closing_issue_numbers_ignores_refs() -> None:
    assert pr_closing_issue_numbers("Refs #5", "Closes #10") == frozenset({10})


def test_duplicate_head_evidence_requires_matching_ref_or_sha() -> None:
    winner = PullView(1, "", "", "codex", head_ref="codex/x", head_sha="sha1")
    assert duplicate_head_evidence(winner, PullView(2, "", "", "codex", head_ref="codex/x", head_sha="other"))
    assert duplicate_head_evidence(winner, PullView(3, "", "", "codex", head_ref="codex/y", head_sha="sha1"))
    assert not duplicate_head_evidence(winner, PullView(4, "", "", "codex", head_ref="codex/y", head_sha="sha2"))


def test_run_closer_skips_same_issue_different_closing_sets() -> None:
    closed: list[int] = []

    def payload(number: int, body: str, ref: str) -> dict:
        return {
            "number": number,
            "title": "",
            "body": body,
            "labels": [{"name": "agent:cursor"}],
            "user": {"login": "cursor[bot]", "type": "Bot"},
            "head": {"ref": ref, "sha": f"sha{number}"},
        }

    run_closer(
        "org/Runner_Dashboard",
        pulls=[payload(1, "Closes #3595", "cursor/a"), payload(2, "Closes #3595\nCloses #3596", "cursor/b")],
        dry_run=False,
        agent_filter=None,
        close_pr=lambda _r, n: closed.append(n),
        comment=lambda *_: None,
    )
    assert closed == []
