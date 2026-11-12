"""Tests for A4 andon triage in the existing Barb dispatcher lane (#2903)."""

from __future__ import annotations

import datetime as _dt

import pytest

if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

from backend.dispatch.andon_triage import (
    BARB_TRIAGE_SCOPE,
    AndonRecord,
    build_triage_card,
    dispatch_red_main_p0,
    rank_andons,
)


def test_rank_andons_red_main_first() -> None:
    """Ranked list places red_main=True first, then older/higher-severity items."""
    older_pr_andon = AndonRecord(
        fingerprint="fp-pr-older",
        issue_number=101,
        opened_at="2026-10-29T10:00:00Z",
        red_main=False,
        severity="high",
        title="[andon] CI Standard / tests: backend/foo.py",
        summary="Unit test failure on PR",
    )
    newer_red_main = AndonRecord(
        fingerprint="fp-main-red",
        issue_number=102,
        opened_at="2026-10-29T12:00:00Z",
        red_main=True,
        severity="critical",
        title="[andon] CI Standard / lint: backend/bar.py",
        summary="Red main failure on default branch",
    )
    gate_error_escalation = AndonRecord(
        fingerprint="fp-gate-error",
        issue_number=103,
        opened_at="2026-10-29T09:00:00Z",
        red_main=False,
        severity="high",
        source="gate-error",
        title="[andon] gate-error escalation",
        summary="Gate error routed from A6/A7",
    )

    ranked = rank_andons([older_pr_andon, newer_red_main, gate_error_escalation])

    assert [r.fingerprint for r in ranked] == [
        "fp-main-red",
        "fp-gate-error",
        "fp-pr-older",
    ]
    assert ranked[0].red_main is True


def test_build_triage_card_links_single_fingerprint() -> None:
    """build_triage_card creates an issue-shaped card with claim:<agent> and 1 fingerprint."""
    record = AndonRecord(
        fingerprint="fp-single-card",
        issue_number=2902,
        opened_at="2026-10-29T11:30:00Z",
        red_main=False,
        severity="high",
        repo="D-sorganization/Runner_Dashboard",
        title="[andon] CI Standard / tests: backend/andon/intake.py",
        summary="AssertionError in intake test",
    )

    card = build_triage_card(record, claim_agent="antigravity")

    assert card["scope"] == BARB_TRIAGE_SCOPE
    assert card["linked_fingerprints"] == ["fp-single-card"]
    assert len(card["linked_fingerprints"]) == 1
    assert "claim:antigravity" in card["labels"]
    claim_labels = [lbl for lbl in card["labels"] if lbl.startswith("claim:")]
    assert claim_labels == ["claim:antigravity"]
    assert card["source_issue_number"] == 2902
    assert isinstance(card["acceptance_criteria"], list)
    assert len(card["acceptance_criteria"]) >= 1
    assert "fp-single-card" in card["body"]


def test_dispatch_red_main_p0_dedupes_duplicate_fingerprints() -> None:
    """Duplicate red-main fingerprints emit at most one P0 auto-dispatch."""
    dispatched_cards: list[dict[str, object]] = []

    records = [
        AndonRecord(
            fingerprint="fp-red-main-dup",
            issue_number=201,
            opened_at="2026-10-29T12:00:00Z",
            red_main=True,
            severity="p0",
            title="[andon] main red #1",
        ),
        AndonRecord(
            fingerprint="fp-red-main-dup",
            issue_number=202,
            opened_at="2026-10-29T12:05:00Z",
            red_main=True,
            severity="p0",
            title="[andon] main red duplicate burst",
        ),
        AndonRecord(
            fingerprint="fp-non-main",
            issue_number=203,
            opened_at="2026-10-29T11:00:00Z",
            red_main=False,
            severity="high",
            title="[andon] PR failure",
        ),
    ]

    emitted = dispatch_red_main_p0(
        records,
        claim_agent="claude",
        dispatcher=dispatched_cards.append,
    )

    assert len(emitted) == 1
    assert len(dispatched_cards) == 1
    assert emitted[0]["linked_fingerprints"] == ["fp-red-main-dup"]
    assert emitted[0]["priority"] == "P0"
    assert "claim:claude" in emitted[0]["labels"]


def test_andon_triage_preconditions_reject_invalid_inputs() -> None:
    """DbC: invalid AndonRecord fields or unknown claim agent raise ValueError."""
    with pytest.raises(ValueError, match="fingerprint"):
        AndonRecord(fingerprint="", issue_number=1, opened_at="2026-10-29T10:00:00Z")

    with pytest.raises(ValueError, match="issue_number"):
        AndonRecord(fingerprint="fp-valid", issue_number=0, opened_at="2026-10-29T10:00:00Z")

    with pytest.raises(ValueError, match="opened_at"):
        AndonRecord(fingerprint="fp-valid", issue_number=1, opened_at="not-a-timestamp")

    valid = AndonRecord(
        fingerprint="fp-valid",
        issue_number=10,
        opened_at="2026-10-29T10:00:00Z",
        red_main=True,
    )
    with pytest.raises(ValueError, match="Unknown agent id"):
        build_triage_card(valid, claim_agent="not-a-fleet-agent")
