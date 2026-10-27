"""Out-of-band hub liveness sentinel on Brick (#3050), slice 3/3: the
``run_once`` CLI wiring. Probe classification and the alert/cooldown/
recovery state machine are tested in ``test_hub_sentinel.py`` (slice 1);
the alert-sink port contract is tested in ``test_hub_sentinel_alerts.py``
(slice 2). This is the last piece: dry-run never writes state or calls
sinks, a live run does both.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

import hub_sentinel_cli as cli  # noqa: E402

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


class _RecordingSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


def test_dry_run_is_a_no_op_but_a_live_run_persists_and_alerts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(cli, "probe_hub", lambda *_a, **_k: ("transport_down", "refused"))

    dry_sink = _RecordingSink()
    for _ in range(3):
        cli.run_once(hub_url="http://brick:8321", state_path=state_path, sinks=[dry_sink], dry_run=True, now=NOW)
    assert not state_path.exists()
    assert dry_sink.messages == []

    live_sink = _RecordingSink()
    events: list[cli.SentinelEvent] = []
    for _ in range(3):
        events = cli.run_once(
            hub_url="http://brick:8321", state_path=state_path, sinks=[live_sink], dry_run=False, now=NOW
        )
    assert state_path.exists()
    assert len(events) == 1
    assert live_sink.messages == ["hub unreachable: transport_down (refused)"]
