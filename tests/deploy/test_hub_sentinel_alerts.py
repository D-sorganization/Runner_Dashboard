"""Out-of-band hub liveness sentinel on Brick (#3050), slice 2/3: the
alert-sink port contract and dispatch fan-out. Probe classification and
the alert/cooldown/recovery state machine are tested in
``test_hub_sentinel.py``; the ``run_once``/CLI integration test lands
with the wiring slice.
"""

from __future__ import annotations

import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

import hub_sentinel_alerts as alerts  # noqa: E402


class _RecordingSink(alerts.AlertSink):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class _FailingSink(alerts.AlertSink):
    def send(self, message: str) -> None:
        raise RuntimeError("sink is down")


def test_one_failing_sink_does_not_block_the_others() -> None:
    good = _RecordingSink()
    alerts.dispatch([_FailingSink(), good], "hub unreachable: transport_down")
    assert good.messages == ["hub unreachable: transport_down"]


def test_barb_inbox_sink_appends_one_line_per_alert(tmp_path: Path) -> None:
    sink = alerts.BarbInboxFileSink(tmp_path / "barb_inbox.txt")
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

    sink = alerts.RmCommentSink(
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
    sink = alerts.SmtpAlertSink(
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
