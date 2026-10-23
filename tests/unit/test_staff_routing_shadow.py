"""Success definition + shadow-mode routing advice (epic #2117)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from staff import routing_shadow
from staff.report_cards import build_report_cards
from staff.run_outcome import BUCKETS, classify_outcome
from staff.store import RunRecord, RunStore

_NOW = datetime.now(UTC) - timedelta(hours=1)
_TS = _NOW.isoformat().replace("+00:00", "Z")


def _run(run_id: str, *, provider: str = "claude", status: str = "succeeded", verification: str = "verified", **kw):
    return RunRecord(
        id=run_id,
        role=kw.pop("role", "issue-remediator"),
        provider=provider,
        model="m",
        machine="M",
        repo="r",
        target_kind="prompt",
        target_ref="",
        prompt="p",
        status=status,
        verification=verification,
        created_at=_TS,
        started_at=_TS,
        ended_at=_TS,
        exit_code=0 if status == "succeeded" else 1,
        **kw,
    )


@pytest.mark.parametrize(
    ("status", "verification", "failure_class", "bucket"),
    [
        ("succeeded", "verified", "", "success"),
        ("succeeded", "no_change", "", "no_op"),
        ("succeeded", "not_applicable", "", "no_op"),
        ("succeeded", "", "", "no_op"),
        ("succeeded", "unverified", "", "no_op"),
        ("succeeded", "failed", "", "quality_fail"),
        ("failed", "", "unverified_output", "quality_fail"),
        ("failed", "", "timeout", "quality_fail"),
        ("failed", "", "", "quality_fail"),
        ("failed", "", "rate_limited", "infra_fail"),
        ("failed", "", "quota_exhausted", "infra_fail"),
        ("failed", "", "auth_expired", "infra_fail"),
        ("failed", "", "provider_capacity", "infra_fail"),
        ("failed", "", "environment_error", "infra_fail"),
        ("blocked", "", "", "infra_fail"),
        ("cancelled", "", "", "cancelled"),
        ("queued", "", "", "in_flight"),
        ("running", "", "", "in_flight"),
        ("needs_input", "", "", "in_flight"),
    ],
)
def test_classify_outcome_table(status: str, verification: str, failure_class: str, bucket: str) -> None:
    got = classify_outcome(status, verification, failure_class)
    assert got == bucket and got in BUCKETS


def _card(runs: list[RunRecord]) -> dict:
    cards = build_report_cards(runs)["leaderboard"]
    assert len(cards) == 1
    return cards[0]


def test_only_verified_runs_count_as_success_and_neutral_is_excluded_from_n() -> None:
    runs = [_run("a", verification="verified"), _run("b", verification="no_change"), _run("c", verification="")]
    card = _card(runs)
    assert card["succeeded_runs"] == 1 and card["terminal_runs"] == 1
    assert card["neutral_runs"] == 2


def test_infra_failures_do_not_lower_quality_but_lower_availability() -> None:
    base = [_run(f"s{i}") for i in range(4)]
    infra = [_run(f"i{i}", status="failed", verification="", failure_class="rate_limited") for i in range(4)]
    clean, noisy = _card(base), _card(base + infra)
    assert noisy["first_pass_success_rate"] == clean["first_pass_success_rate"] == 1.0
    assert noisy["first_pass_ci_95"] == clean["first_pass_ci_95"]
    assert noisy["terminal_runs"] == 4 and noisy["infra_failed_runs"] == 4
    assert clean["availability"] == 1.0 and noisy["availability"] == 0.5


def test_quality_failures_lower_the_score() -> None:
    good = [_run(f"s{i}") for i in range(4)]
    bad = [_run(f"f{i}", status="failed", verification="", failure_class="no_result") for i in range(4)]
    assert _card(good + bad)["first_pass_success_rate"] == 0.5


def _history(provider: str, ok: int, bad: int) -> list[RunRecord]:
    return [_run(f"{provider}-ok{i}", provider=provider) for i in range(ok)] + [
        _run(f"{provider}-bad{i}", provider=provider, status="failed", verification="failed") for i in range(bad)
    ]


def test_advice_requires_min_n_before_recommending() -> None:
    advice = routing_shadow.advise(
        _history("codex", 7, 0), role="issue-remediator", chosen="claude", complexity_tier="standard"
    )
    assert advice["recommended"] is None and advice["agrees"] is None
    assert advice["scores"]["codex"]["n"] == 7


def test_advice_recommends_best_eligible_provider() -> None:
    runs = _history("codex", 9, 1) + _history("claude", 4, 6) + _history("antigravity", 3, 0)
    advice = routing_shadow.advise(runs, role="issue-remediator", chosen="claude", complexity_tier="standard")
    assert advice["recommended"] == "codex" and advice["agrees"] is False
    assert set(advice["scores"]) == {"codex", "claude", "antigravity"}  # antigravity scored but n<8


@pytest.fixture
def store(tmp_path: Path):
    s = RunStore(tmp_path / "runs.sqlite3")
    yield s
    s.close()


def _events(store: RunStore, run_id: str) -> list:
    return [e for e in store.events_after(run_id) if e["kind"] == routing_shadow.EVENT_KIND]


def test_mode_defaults_off_and_records_nothing(store: RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(routing_shadow.ENV_MODE, raising=False)
    assert routing_shadow.mode() == "off"
    rec = store.create_run(_run("new", provider="claude", status="queued", verification=""))
    assert routing_shadow.record_for_run(store, rec) is None
    assert _events(store, "new") == []


def test_shadow_mode_records_event_without_changing_choice(store: RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(routing_shadow.ENV_MODE, "shadow")
    for r in _history("codex", 9, 0):
        store.create_run(r)
    rec = store.create_run(_run("new", provider="claude", status="queued", verification=""))
    advice = routing_shadow.record_for_run(store, rec)
    assert advice is not None and advice["chosen"] == "claude" and advice["recommended"] == "codex"
    (event,) = _events(store, "new")
    body = json.loads(event["text"])
    assert body["chosen"] == "claude" and body["recommended"] == "codex" and "scores" in body
    assert store.get_run("new").provider == "claude"


def test_shadow_failure_never_raises(store: RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(routing_shadow.ENV_MODE, "shadow")
    monkeypatch.setattr(store, "list_runs", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))
    assert routing_shadow.record_for_run(store, _run("x", status="queued", verification="")) is None
