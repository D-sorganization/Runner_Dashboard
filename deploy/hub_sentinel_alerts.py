#!/usr/bin/env python3
"""Alert sinks and dispatch fan-out for the hub liveness sentinel (#3050).

A pluggable ``AlertSink`` port with three implementations (a local Barb
inbox file, SMTP, and an RM #2376 comment via the fleet ``gh`` wrapper)
plus ``dispatch()``, which fans a single alert message out to every
configured sink and never lets one failing sink block the others.

Kept in its own flat module, separate from ``hub_sentinel.py``'s probe
and state-machine core, so each half of #3050 is independently testable;
the CLI entrypoint that wires the two together lands in a later slice.
"""

from __future__ import annotations

import logging
import smtplib
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("hub_sentinel_alerts")


class AlertSink:
    """Port: every sink implements ``send``; a failing sink must never
    block the others (see ``dispatch``)."""

    def send(self, message: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class BarbInboxFileSink(AlertSink):
    """Appends one timestamped line per alert to a local Barb inbox file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def send(self, message: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(UTC).isoformat()} {message}\n")


class SmtpAlertSink(AlertSink):
    """Optional SMTP sink, config-selected (``HUB_SENTINEL_SMTP_TO``); never
    logs or includes credentials in the message it sends."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_addr: str,
        to_addr: str,
        send_message: Callable[[EmailMessage], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._from_addr = from_addr
        self._to_addr = to_addr
        self._send_message = send_message or self._send_via_smtplib

    def send(self, message: str) -> None:
        email = EmailMessage()
        email["Subject"] = "RD hub sentinel"
        email["From"] = self._from_addr
        email["To"] = self._to_addr
        email.set_content(message)
        self._send_message(email)

    def _send_via_smtplib(self, email: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=10) as client:
            client.send_message(email)


class RmCommentSink(AlertSink):
    """Posts to RM #2376 via the fleet ``gh`` wrapper (``deploy/fleet-gh/gh``),
    which mints its own App token internally; the token never appears in our
    command line, environment overrides or logs."""

    def __init__(
        self,
        *,
        gh_path: str,
        repo: str,
        issue: int,
        run: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._gh_path = gh_path
        self._repo = repo
        self._issue = issue
        self._run = run or self._default_run

    def send(self, message: str) -> None:
        self._run([self._gh_path, "issue", "comment", str(self._issue), "--repo", self._repo, "--body", message])

    @staticmethod
    def _default_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, check=True, text=True, capture_output=True)  # noqa: S603


def dispatch(sinks: list[AlertSink], message: str) -> None:
    for sink in sinks:
        try:
            sink.send(message)
        except Exception:  # noqa: BLE001 - one failing sink must not block the others
            log.exception("alert sink failed to send")


def build_sinks_from_env(environ: dict[str, str]) -> list[AlertSink]:
    sinks: list[AlertSink] = []
    inbox = environ.get("HUB_SENTINEL_BARB_INBOX", "").strip()
    if inbox:
        sinks.append(BarbInboxFileSink(Path(inbox).expanduser()))
    smtp_to = environ.get("HUB_SENTINEL_SMTP_TO", "").strip()
    if smtp_to:
        sinks.append(
            SmtpAlertSink(
                host=environ.get("HUB_SENTINEL_SMTP_HOST", "localhost"),
                port=int(environ.get("HUB_SENTINEL_SMTP_PORT", "25")),
                from_addr=environ.get("HUB_SENTINEL_SMTP_FROM", "hub-sentinel@runner-dashboard"),
                to_addr=smtp_to,
            )
        )
    rm_repo = environ.get("HUB_SENTINEL_RM_REPO", "").strip()
    if rm_repo:
        sinks.append(
            RmCommentSink(
                gh_path=environ.get("HUB_SENTINEL_GH_PATH", "gh"),
                repo=rm_repo,
                issue=int(environ.get("HUB_SENTINEL_RM_ISSUE", "2376")),
            )
        )
    return sinks
