"""Live subscription quota for staff providers (issue #1587).

Payload shapes below were captured on OGLaptop on 2026-09-26: a Claude Code
2.1.280 ``rate_limit_event`` from ``claude -p --output-format stream-json``, a
Claude status-line ``rate_limits`` block, and a Codex 0.156.1 session-log
``token_count`` event.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from staff import quota

NOW = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)
FIVE_H_RESET = 1790454000  # 2026-09-26T20:20:00Z
SEVEN_D_RESET = 1790949600  # 2026-10-02T13:00:00Z

CLAUDE_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "resetsAt": FIVE_H_RESET,
        "rateLimitType": "five_hour",
        "overageStatus": "rejected",
        "isUsingOverage": False,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.04, "resetsAt": FIVE_H_RESET},
            "seven_day": {"utilization": 0.23, "resetsAt": SEVEN_D_RESET},
        },
    },
    "session_id": "s",
}

CODEX_LINE = {
    "timestamp": "2026-09-26T17:30:00.000Z",
    "type": "event_msg",
    "payload": {
        "type": "token_count",
        "info": {"total_token_usage": {"input_tokens": 1}},
        "rate_limits": {
            "limit_id": "codex",
            "primary": {"used_percent": 97.0, "window_minutes": 10080, "resets_at": SEVEN_D_RESET},
            "secondary": {"used_percent": 12.5, "window_minutes": 300, "resets_at": FIVE_H_RESET},
            "credits": {"has_credits": False, "unlimited": False, "balance": "0E-10"},
            "plan_type": "pro",
            "rate_limit_reached_type": None,
        },
    },
}


def _write_session(root: Path, day: str, name: str, lines: list[dict], mtime: float | None = None) -> Path:
    path = root / day / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# ── parsers ──────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_claude_rate_limit_event_becomes_percent_windows() -> None:
    snap = quota.from_claude_event(CLAUDE_EVENT, observed_at=NOW)
    assert snap is not None and snap.account == "claude" and snap.source == "claude-stream"
    windows = {w.name: w for w in snap.windows}
    assert windows["five_hour"].used_percent == pytest.approx(4.0)
    assert windows["seven_day"].used_percent == pytest.approx(23.0)
    assert windows["seven_day"].resets_at == datetime.fromtimestamp(SEVEN_D_RESET, UTC)
    assert snap.limited_until is None


@pytest.mark.unit
def test_rejected_claude_status_marks_the_account_limited_until_reset() -> None:
    event = json.loads(json.dumps(CLAUDE_EVENT))
    event["rate_limit_info"]["status"] = "rejected"
    snap = quota.from_claude_event(event, observed_at=NOW)
    assert snap is not None and snap.limited_until == datetime.fromtimestamp(FIVE_H_RESET, UTC)


@pytest.mark.unit
def test_claude_event_without_unified_windows_uses_the_headline_window() -> None:
    event = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "resetsAt": FIVE_H_RESET,
            "rateLimitType": "five_hour",
            "utilization": 0.5,
        },
    }
    snap = quota.from_claude_event(event, observed_at=NOW)
    assert snap is not None and [(w.name, w.used_percent) for w in snap.windows] == [("five_hour", 50.0)]


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw", [{"type": "result"}, {"type": "rate_limit_event"}, {"type": "rate_limit_event", "rate_limit_info": {}}]
)
def test_non_quota_events_parse_to_none(raw: dict) -> None:
    assert quota.from_claude_event(raw, observed_at=NOW) is None


@pytest.mark.unit
def test_claude_statusline_rate_limits_use_percentages() -> None:
    payload = {
        "rate_limits": {
            "five_hour": {"used_percentage": 65, "resets_at": FIVE_H_RESET},
            "seven_day": {"used_percentage": 42, "resets_at": SEVEN_D_RESET},
        }
    }
    snap = quota.from_claude_statusline(payload, observed_at=NOW)
    assert snap is not None and snap.source == "claude-statusline"
    assert {w.name: w.used_percent for w in snap.windows} == {"five_hour": 65.0, "seven_day": 42.0}
    assert quota.from_claude_statusline({"model": {}}, observed_at=NOW) is None


@pytest.mark.unit
def test_codex_token_count_event_names_windows_by_length() -> None:
    snap = quota.from_codex_line(CODEX_LINE)
    assert snap is not None and snap.account == "codex" and snap.plan == "pro"
    assert snap.observed_at == datetime(2026, 9, 26, 17, 30, tzinfo=UTC)
    assert {w.name: w.used_percent for w in snap.windows} == {"seven_day": 97.0, "five_hour": 12.5}


@pytest.mark.unit
def test_codex_reached_limit_marks_the_account_limited() -> None:
    line = json.loads(json.dumps(CODEX_LINE))
    line["payload"]["rate_limits"]["rate_limit_reached_type"] = "primary"
    snap = quota.from_codex_line(line)
    assert snap is not None and snap.limited_until == datetime.fromtimestamp(SEVEN_D_RESET, UTC)


@pytest.mark.unit
def test_codex_odd_window_lengths_keep_their_minutes() -> None:
    line = json.loads(json.dumps(CODEX_LINE))
    line["payload"]["rate_limits"]["primary"]["window_minutes"] = 1440
    line["payload"]["rate_limits"]["secondary"] = None
    snap = quota.from_codex_line(line)
    assert snap is not None and [w.name for w in snap.windows] == ["1440_minute"]


# ── windows expire at reset ──────────────────────────────────────────────
@pytest.mark.unit
def test_current_drops_windows_whose_reset_has_passed() -> None:
    snap = quota.from_claude_event(CLAUDE_EVENT, observed_at=NOW)
    assert snap is not None
    later = datetime.fromtimestamp(FIVE_H_RESET, UTC) + timedelta(minutes=1)
    assert [w.name for w in snap.current(later).windows] == ["seven_day"]
    assert snap.current(later).peak() is not None and snap.current(later).peak().name == "seven_day"


@pytest.mark.unit
def test_limit_lifts_at_its_reset_time() -> None:
    line = json.loads(json.dumps(CODEX_LINE))
    line["payload"]["rate_limits"]["rate_limit_reached_type"] = "secondary"
    snap = quota.from_codex_line(line)
    assert snap is not None and snap.limited_until == datetime.fromtimestamp(FIVE_H_RESET, UTC)
    assert snap.current(datetime.fromtimestamp(FIVE_H_RESET + 1, UTC)).limited_until is None


# ── codex session logs ───────────────────────────────────────────────────
@pytest.mark.unit
def test_codex_reader_takes_the_newest_rate_limits_from_the_newest_session(tmp_path: Path) -> None:
    old = json.loads(json.dumps(CODEX_LINE))
    old["timestamp"] = "2026-09-20T00:00:00Z"
    old["payload"]["rate_limits"]["primary"]["used_percent"] = 10.0
    _write_session(tmp_path, "2026/09/20", "rollout-a.jsonl", [old], mtime=1_000)
    newer = json.loads(json.dumps(CODEX_LINE))
    newer["timestamp"] = "2026-09-26T17:45:00Z"
    newer["payload"]["rate_limits"]["primary"]["used_percent"] = 98.0
    _write_session(
        tmp_path,
        "2026/09/26",
        "rollout-b.jsonl",
        [CODEX_LINE, newer, {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": None}}],
        mtime=2_000,
    )
    snap = quota.read_codex_sessions([tmp_path])
    assert snap is not None and snap.source == "codex-session-log"
    assert {w.name: w.used_percent for w in snap.windows}["seven_day"] == 98.0


@pytest.mark.unit
def test_codex_reader_survives_missing_dirs_and_garbage(tmp_path: Path) -> None:
    _write_session(tmp_path, "2026/09/26", "rollout-x.jsonl", [])
    (tmp_path / "2026/09/26/rollout-x.jsonl").write_text('not json\n{"broken\n', encoding="utf-8")
    assert quota.read_codex_sessions([tmp_path, tmp_path / "missing"]) is None


@pytest.mark.unit
def test_codex_session_dirs_honour_the_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STAFF_CODEX_SESSION_DIRS", os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")]))
    assert quota.codex_session_dirs() == [tmp_path / "a", tmp_path / "b"]
    monkeypatch.delenv("STAFF_CODEX_SESSION_DIRS")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    assert quota.codex_session_dirs() == [tmp_path / "home" / "sessions"]


# ── store ────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_store_keeps_the_newest_snapshot_per_account(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    first = quota.from_claude_event(CLAUDE_EVENT, observed_at=NOW)
    older = quota.from_claude_event(CLAUDE_EVENT, observed_at=NOW - timedelta(hours=1))
    assert first is not None and older is not None
    assert store.record(first) is True
    assert store.record(older) is False  # never regress to stale data
    reloaded = quota.QuotaStore(tmp_path / "q.json").get("claude")
    assert reloaded == first


@pytest.mark.unit
def test_store_tolerates_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text("{not json", encoding="utf-8")
    store = quota.QuotaStore(path)
    assert store.get("claude") is None
    snap = quota.from_claude_event(CLAUDE_EVENT, observed_at=NOW)
    assert snap is not None and store.record(snap) and store.get("claude") == snap


# ── per-provider view ────────────────────────────────────────────────────
@pytest.mark.unit
def test_provider_billing_and_quota_accounts() -> None:
    assert quota.billing("claude") == "subscription" and quota.billing("codex") == "subscription"
    assert quota.billing("ollama") == "local" and quota.billing("claude-ollama") == "local"
    assert quota.billing("unknown-cli") == "unknown"
    assert quota.QUOTA_ACCOUNT["claude"] == "claude" and quota.QUOTA_ACCOUNT["codex"] == "codex"
    assert "ollama" not in quota.QUOTA_ACCOUNT  # codex --oss runs on local models, not the ChatGPT plan


@pytest.mark.unit
def test_for_provider_merges_store_and_session_logs_newest_first(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    stored = quota.from_codex_line(CODEX_LINE)
    assert stored is not None
    store.record(stored)
    newer = json.loads(json.dumps(CODEX_LINE))
    newer["timestamp"] = "2026-09-26T17:59:00Z"
    newer["payload"]["rate_limits"]["primary"]["used_percent"] = 99.0
    _write_session(tmp_path / "sessions", "2026/09/26", "r.jsonl", [newer])
    snap = quota.for_provider("codex", NOW, store=store, codex_dirs=[tmp_path / "sessions"])
    assert snap is not None and snap.peak() is not None and snap.peak().used_percent == 99.0
    assert quota.for_provider("ollama", NOW, store=store, codex_dirs=[]) is None


@pytest.mark.unit
def test_observe_records_only_claude_account_events(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    assert quota.observe("claude", CLAUDE_EVENT, store=store, now=NOW) is True
    assert quota.observe("claude-ollama", CLAUDE_EVENT, store=store, now=NOW) is False
    assert quota.observe("claude", {"type": "result"}, store=store, now=NOW) is False
    assert store.get("claude") is not None


@pytest.mark.unit
def test_report_lists_every_provider_with_billing_and_windows(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "q.json")
    snap = quota.from_claude_event(CLAUDE_EVENT, observed_at=NOW)
    assert snap is not None
    store.record(snap)
    body = quota.report(["claude", "codex", "ollama"], NOW, store=store, codex_dirs=[])
    rows = {row["provider"]: row for row in body["providers"]}
    assert rows["claude"]["billing"] == "subscription" and rows["claude"]["readable"] is True
    assert rows["claude"]["quota"]["peak_percent"] == pytest.approx(23.0)
    assert rows["claude"]["quota"]["windows"][0]["resets_at"].endswith("Z")
    assert rows["codex"]["readable"] is True and rows["codex"]["quota"] is None  # no data yet
    assert rows["ollama"]["readable"] is False and rows["ollama"]["billing"] == "local"
    assert body["generated_at"] == "2026-09-26T18:00:00Z"


# ── wiring ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_quota_route_reports_every_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer, require_orchestrator_peer
    from routers import staff_usage as usage_router
    from staff.adapters import ADAPTERS

    monkeypatch.setenv("STAFF_QUOTA_STATE", str(tmp_path / "q.json"))
    monkeypatch.setenv("STAFF_CODEX_SESSION_DIRS", str(tmp_path / "none"))
    snap = quota.from_claude_event(CLAUDE_EVENT, observed_at=datetime.now(UTC))
    assert snap is not None
    quota.default_store().record(snap)
    app = FastAPI()
    app.include_router(usage_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_orchestrator_peer] = lambda: "test-orchestrator"
    with TestClient(app) as c:
        body = c.get("/api/staff/quota").json()
    rows = {row["provider"]: row for row in body["providers"]}
    assert set(rows) == set(ADAPTERS)
    assert rows["claude"]["quota"]["source"] == "claude-stream"
    assert rows["codex"]["quota"] is None and rows["gemini"]["readable"] is False


@pytest.mark.unit
def test_runner_records_claude_rate_limit_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from staff import runner as runner_mod
    from staff.adapters import ADAPTERS
    from staff.store import RunStore

    monkeypatch.setenv("STAFF_QUOTA_STATE", str(tmp_path / "q.json"))
    store = RunStore(tmp_path / "runs.sqlite3")
    runner = runner_mod.StaffRunner(store=store, adapters=dict(ADAPTERS), machine="T")

    class _Proc:
        stdout = io.StringIO(
            json.dumps(CLAUDE_EVENT) + "\n" + json.dumps({"type": "result", "result": "STAFF_RESULT: ok"}) + "\n"
        )

    rec = type("Rec", (), {"id": "run-1", "provider": "claude"})()
    usage, result_line = runner._pump_output(rec, ADAPTERS["claude"], _Proc(), tmp_path / "t.log")  # type: ignore[arg-type]
    assert result_line == "STAFF_RESULT: ok"
    stored = quota.default_store().get("claude")
    assert stored is not None and {w.name for w in stored.windows} == {"five_hour", "seven_day"}
    store.close()


@pytest.mark.unit
def test_usage_monitoring_turns_live_quota_into_subscription_sources() -> None:
    import usage_monitoring as um

    snap = quota.from_codex_line(CODEX_LINE)
    assert snap is not None
    body = {
        "generated_at": "2026-09-26T18:00:00Z",
        "providers": [
            {"provider": "codex", "billing": "subscription", "readable": True, "quota": snap.to_dict()},
            {"provider": "claude", "billing": "subscription", "readable": True, "quota": None},
            {"provider": "ollama", "billing": "local", "readable": False, "quota": None},
        ],
    }
    sources = um.quota_usage_sources(body)
    assert [s["name"] for s in sources] == ["codex_plan"]
    src = sources[0]
    assert src["kind"] == "subscription" and src["usage_unit"] == "percent" and src["usage_limit"] == 100
    assert src["current_usage"] == 97.0 and src["current_period"]["label"] == "seven_day"
    assert src["last_refresh"] == "2026-09-26T17:30:00Z"
    summary = um.normalize_usage_summary(sources)
    assert summary["usage_sources"][0]["remaining"] == 3.0


@pytest.mark.unit
def test_usage_sources_fixture_carries_no_invented_figures() -> None:
    import usage_monitoring as um

    assert um.load_usage_sources_config(um.DEFAULT_USAGE_SOURCES_PATH) == []
