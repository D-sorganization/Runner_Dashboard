"""Fleet efficiency: wake coalescing, lane brief cap, planned reset thresholds (#4562)."""

from __future__ import annotations

import time

import pytest

from coordination.fleet_efficiency import (
    BriefValidationError,
    WakeCoalescer,
    enrich_lane_receipt,
    evaluate_planned_reset,
    fleet_efficiency_metadata,
    format_lane_brief,
    validate_lane_brief,
    wake_coalesce_seconds,
)


@pytest.mark.unit
def test_wake_coalesce_seconds_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_WAKE_COALESCE_SEC", raising=False)
    assert wake_coalesce_seconds() == 300
    monkeypatch.setenv("FLEET_WAKE_COALESCE_SEC", "240")
    assert wake_coalesce_seconds() == 300
    monkeypatch.setenv("FLEET_WAKE_COALESCE_SEC", "450")
    assert wake_coalesce_seconds() == 450
    monkeypatch.setenv("FLEET_WAKE_COALESCE_SEC", "900")
    assert wake_coalesce_seconds() == 600


@pytest.mark.unit
def test_wake_coalescer_batches_within_window() -> None:
    co = WakeCoalescer(window_sec=300)
    t0 = 1_000.0
    assert co.should_deliver_wake("lane_done", "rd-barb", now=t0) is True
    co.record_wake("lane_done", "rd-barb", now=t0)
    assert co.should_deliver_wake("lane_done", "rd-barb", now=t0 + 100) is False
    assert co.should_deliver_wake("lane_done", "rd-barb", now=t0 + 301) is True


@pytest.mark.unit
def test_wake_coalescer_independent_keys() -> None:
    co = WakeCoalescer(window_sec=300)
    t0 = 5_000.0
    co.record_wake("lane_done", "codex", now=t0)
    assert co.should_deliver_wake("ci_wake", "rd-barb", now=t0 + 10) is True


@pytest.mark.unit
def test_format_and_validate_lane_brief_six_line_cap() -> None:
    text = format_lane_brief(
        status="stopped",
        result="PR opened",
        evidence="Checks green",
        evidence_path="https://example.com/pr/1",
    )
    parsed = validate_lane_brief(text, evidence_path="https://example.com/pr/1")
    assert parsed["status"] == "stopped"
    assert parsed["result"] == "PR opened"
    assert parsed["evidence_path"] == "https://example.com/pr/1"
    assert len(text.splitlines()) <= 6


@pytest.mark.unit
def test_validate_lane_brief_rejects_too_many_lines() -> None:
    body = "\n".join(f"line-{i}" for i in range(8))
    with pytest.raises(BriefValidationError, match="6-line"):
        validate_lane_brief(body, evidence_path="/tmp/log.txt")


@pytest.mark.unit
def test_validate_lane_brief_requires_evidence_path() -> None:
    text = format_lane_brief(
        status="idle",
        result="queue empty",
        evidence="n/a",
        evidence_path="file:///session/lane-event.txt",
    )
    validate_lane_brief(text, evidence_path=None)
    with pytest.raises(BriefValidationError, match="Evidence path"):
        validate_lane_brief(text.replace("Evidence path:", "Detail:"), evidence_path=None)


@pytest.mark.unit
def test_planned_reset_thresholds_metadata() -> None:
    meta = fleet_efficiency_metadata()
    assert meta["wake_coalesce_sec"] >= 300
    assert meta["brief_line_cap"] == 6
    assert meta["planned_reset"]["min_turns"] >= 25
    assert meta["planned_reset"]["min_tokens_per_turn"] >= 150_000


@pytest.mark.unit
def test_evaluate_planned_reset() -> None:
    ok = evaluate_planned_reset(turn_count=5, tokens_last_turn=10_000)
    assert ok["recommended"] is False
    hit = evaluate_planned_reset(turn_count=30, tokens_last_turn=10_000, post_milestone=False)
    assert hit["recommended"] is True
    assert "turns" in hit["reasons"]
    tok = evaluate_planned_reset(turn_count=3, tokens_last_turn=200_000)
    assert tok["recommended"] is True
    assert "tokens_per_turn" in tok["reasons"]


@pytest.mark.unit
def test_enrich_lane_receipt_adds_metadata() -> None:
    receipt = enrich_lane_receipt({"checkout_id": "lchk-test", "lane_id": "cursor-ct-1"})
    assert "fleet_efficiency" in receipt
    assert receipt["fleet_efficiency"]["brief_line_cap"] == 6


@pytest.mark.unit
def test_wake_coalesce_seconds_from_env_at_import_time_documented() -> None:
    # Config is read per-call, not cached across unrelated tests.
    assert isinstance(wake_coalesce_seconds(), int)