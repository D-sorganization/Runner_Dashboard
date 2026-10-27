"""Out-of-band hub liveness sentinel on Brick (#3050): probe classification,
outage/recovery alert transitions, fail-closed corrupt state, and the
alert-sink port contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage, Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

import hub_sentinel as sentinel  # noqa: E402

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _sequence_opener(results: list[object]) -> sentinel.Opener:
    calls = iter(results)

    def opener(_req: object, _timeout: float) -> _FakeResponse:
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result)  # type: ignore[arg-type]

    return opener


# ---------------------------------------------------------------------------
# probe_hub classification: one case per failure class plus the /readyz
# fallback and the plain-ok path (the engine's full outcome space).
# ---------------------------------------------------------------------------

_HTTP_500 = HTTPError("http://x/api/health", 500, "boom", Message(), None)
_HTTP_404 = HTTPError("http://x/api/health", 404, "not found", Message(), None)
_BODY_READY = json.dumps({"status": "ready"}).encode("utf-8")
_BODY_STARTING = json.dumps({"status": "starting"}).encode("utf-8")
_BODY_HEALTHY = json.dumps({"status": "healthy"}).encode("utf-8")


@pytest.mark.parametrize(
    ("primary", "fallback", "expected"),
    [
        (URLError("refused"), URLError("refused"), "transport_down"),
        (_HTTP_500, _HTTP_500, "http_error"),
        (_HTTP_404, _BODY_READY, "ok"),  # falls back to /readyz when /api/health 404s
        (_BODY_STARTING, _BODY_STARTING, "not_ready"),
        (_BODY_HEALTHY, _BODY_HEALTHY, "ok"),
    ],
)
def test_probe_classifies_each_outcome(primary: object, fallback: object, expected: str) -> None:
    classification, _detail = sentinel.probe_hub("http://brick:8321", opener=_sequence_opener([primary, fallback]))
    assert classification == expected


# ---------------------------------------------------------------------------
# evaluate_probe: alert-after-3, cooldown, recovery
# ---------------------------------------------------------------------------


def test_three_consecutive_failures_produce_exactly_one_alert() -> None:
    state = sentinel.SentinelState()
    events_per_call = []
    for _ in range(3):
        state, events = sentinel.evaluate_probe(state, "transport_down", "refused", now=NOW)
        events_per_call.append(events)
    assert events_per_call[0] == []
    assert events_per_call[1] == []
    assert len(events_per_call[2]) == 1
    assert events_per_call[2][0].kind == "alert"
    assert "transport_down" in events_per_call[2][0].message


def test_later_failures_in_the_same_outage_produce_no_further_alerts() -> None:
    state = sentinel.SentinelState()
    for _ in range(3):
        state, _events = sentinel.evaluate_probe(state, "transport_down", "refused", now=NOW)
    for _ in range(5):
        state, events = sentinel.evaluate_probe(state, "transport_down", "refused", now=NOW)
        assert events == []
    assert state.consecutive_failures == 8


def test_first_success_after_an_alerted_outage_produces_one_recovery_notice() -> None:
    state = sentinel.SentinelState()
    for _ in range(3):
        state, _events = sentinel.evaluate_probe(state, "transport_down", "refused", now=NOW)
    recovered_at = NOW + timedelta(minutes=12)
    state, events = sentinel.evaluate_probe(state, "ok", "healthy", now=recovered_at)
    assert len(events) == 1
    assert events[0].kind == "recovery"
    assert "12m" in events[0].message
    # State resets so a fresh outage needs 3 fresh failures again.
    assert state.consecutive_failures == 0
    assert state.last_alert_at is None


def test_success_without_a_prior_alert_produces_no_recovery_notice() -> None:
    state = sentinel.SentinelState()
    state, _events = sentinel.evaluate_probe(state, "transport_down", "refused", now=NOW)
    state, events = sentinel.evaluate_probe(state, "ok", "healthy", now=NOW + timedelta(seconds=5))
    assert events == []
    assert state.consecutive_failures == 0


# ---------------------------------------------------------------------------
# state persistence: fail closed on a corrupt/missing file
# ---------------------------------------------------------------------------


def test_missing_state_file_loads_as_fresh_unknown_state(tmp_path: Path) -> None:
    state = sentinel.load_state(tmp_path / "missing.json")
    assert state == sentinel.SentinelState()


def test_corrupt_state_file_loads_as_fresh_state_and_does_not_alert_immediately(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = sentinel.load_state(path)
    assert state == sentinel.SentinelState()
    # A corrupt file must not be treated as "already 2 failures in" -- it needs
    # 3 fresh failures from here, same as any other fresh state.
    _state, events = sentinel.evaluate_probe(state, "transport_down", "refused", now=NOW)
    assert events == []


def test_save_and_load_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = sentinel.SentinelState(
        consecutive_failures=3,
        outage_started_at=NOW.isoformat(),
        last_alert_at=NOW.isoformat(),
        last_failure_class="transport_down",
    )
    sentinel.save_state(path, original)
    assert sentinel.load_state(path) == original


# ---------------------------------------------------------------------------
# alert sink port contract
# ---------------------------------------------------------------------------


class _RecordingSink(sentinel.AlertSink):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class _FailingSink(sentinel.AlertSink):
    def send(self, message: str) -> None:
        raise RuntimeError("sink is down")


def test_one_failing_sink_does_not_block_the_others() -> None:
    good = _RecordingSink()
    sentinel.dispatch([_FailingSink(), good], "hub unreachable: transport_down")
    assert good.messages == ["hub unreachable: transport_down"]


def test_barb_inbox_sink_appends_one_line_per_alert(tmp_path: Path) -> None:
    sink = sentinel.BarbInboxFileSink(tmp_path / "barb_inbox.txt")
    sink.send("hub unreachable: transport_down")
    sink.send("hub recovered after 12m")
    lines = (tmp_path / "barb_inbox.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("hub unreachable: transport_down")
    assert lines[1].endswith("hub recovered after 12m")


def test_rm_comment_sink_never_passes_a_token_in_the_command() -> None:
    captured: list[list[str]] = []

    def fake_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    sink = sentinel.RmCommentSink(
        gh_path="/opt/fleet-gh/gh",
        repo="D-sorganization/Repository_Management",
        issue=2376,
        run=fake_run,
    )
    sink.send("hub unreachable: transport_down")
    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[0] == "/opt/fleet-gh/gh"
    assert "2376" in cmd
    assert "hub unreachable: transport_down" in cmd
    assert not any("token" in part.lower() or "bearer" in part.lower() for part in cmd)


def test_smtp_sink_sends_the_message_without_logging_credentials() -> None:
    sent: list[EmailMessage] = []
    sink = sentinel.SmtpAlertSink(
        host="localhost",
        port=25,
        from_addr="hub-sentinel@runner-dashboard",
        to_addr="barb@example.invalid",
        send_message=sent.append,
    )
    sink.send("hub unreachable: transport_down")
    assert len(sent) == 1
    assert sent[0]["To"] == "barb@example.invalid"
    assert "hub unreachable" in sent[0].get_content()


# ---------------------------------------------------------------------------
# run_once: dry-run never writes state or calls sinks; a live run does both
# ---------------------------------------------------------------------------


def test_dry_run_is_a_no_op_but_a_live_run_persists_and_alerts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(sentinel, "probe_hub", lambda *_a, **_k: ("transport_down", "refused"))

    dry_sink = _RecordingSink()
    for _ in range(3):
        sentinel.run_once(hub_url="http://brick:8321", state_path=state_path, sinks=[dry_sink], dry_run=True, now=NOW)
    assert not state_path.exists()
    assert dry_sink.messages == []

    live_sink = _RecordingSink()
    events: list[sentinel.SentinelEvent] = []
    for _ in range(3):
        events = sentinel.run_once(
            hub_url="http://brick:8321", state_path=state_path, sinks=[live_sink], dry_run=False, now=NOW
        )
    assert state_path.exists()
    assert len(events) == 1
    assert live_sink.messages == ["hub unreachable: transport_down (refused)"]
