"""Out-of-band hub liveness sentinel on Brick (#3050), slice 1/3: probe
classification, outage/recovery alert transitions, and fail-closed corrupt
state. Alert-sink tests live in ``test_hub_sentinel_alerts.py``; the
``run_once``/CLI integration test lands with the wiring slice.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from email.message import Message
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
