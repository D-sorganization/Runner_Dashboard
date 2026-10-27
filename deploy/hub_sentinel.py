#!/usr/bin/env python3
"""Out-of-band hub liveness sentinel (#3050).

Runs on a second machine (Brick) and tells Barb/Dieter once per hub outage
and once on recovery, so dispatchers stop sending work into a dead hub.
Pure stdlib and self-contained like every other ``deploy/*.py`` entrypoint
(see ``node-auto-deploy.py``): it must run without the hub's own backend
venv installed, since the whole point is that the hub cannot report its
own outage from inside its own process.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("hub_sentinel")

ALERT_AFTER_FAILURES = 3
DEFAULT_TIMEOUT = 3.0

Opener = Callable[[Request, float], Any]


def _default_opener(req: Request, timeout: float) -> Any:
    return urlopen(req, timeout=timeout)  # noqa: S310


def _probe_endpoint(url: str, timeout: float, opener: Opener) -> tuple[str, str]:
    try:
        with opener(Request(url), timeout) as resp:
            body = resp.read()
    except HTTPError as exc:
        return "http_error", f"HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return "transport_down", str(exc)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "not_ready", "non-JSON response"
    if not isinstance(payload, dict):
        return "not_ready", "unexpected response shape"
    status = str(payload.get("status") or "")
    if status in ("healthy", "ok", "ready"):
        return "ok", status
    return "not_ready", f"status={status or 'unknown'}"


def probe_hub(hub_url: str, *, timeout: float = DEFAULT_TIMEOUT, opener: Opener = _default_opener) -> tuple[str, str]:
    """Classify the hub as ``transport_down | http_error | not_ready | ok``.

    Probes ``{hub_url}/api/health`` first, falling back to ``/readyz``;
    reports the primary endpoint's failure if both fail.
    """
    base = hub_url.rstrip("/")
    classification, detail = _probe_endpoint(f"{base}/api/health", timeout, opener)
    if classification == "ok":
        return classification, detail
    fallback_classification, fallback_detail = _probe_endpoint(f"{base}/readyz", timeout, opener)
    if fallback_classification == "ok":
        return fallback_classification, fallback_detail
    return classification, detail


@dataclass
class SentinelState:
    consecutive_failures: int = 0
    outage_started_at: str | None = None
    last_alert_at: str | None = None
    last_failure_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "consecutive_failures": self.consecutive_failures,
            "outage_started_at": self.outage_started_at,
            "last_alert_at": self.last_alert_at,
            "last_failure_class": self.last_failure_class,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SentinelState:
        return cls(
            consecutive_failures=int(data.get("consecutive_failures") or 0),
            outage_started_at=data.get("outage_started_at"),
            last_alert_at=data.get("last_alert_at"),
            last_failure_class=data.get("last_failure_class"),
        )


@dataclass
class SentinelEvent:
    kind: str  # "alert" | "recovery"
    message: str


def default_state_path() -> Path:
    override = os.environ.get("HUB_SENTINEL_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "runner-dashboard" / "hub_sentinel_state.json"


def load_state(path: Path) -> SentinelState:
    """Missing or corrupt state is "unknown": fail closed to a fresh state,
    never to "already mid-outage", so alerting needs 3 fresh failures."""
    if not path.is_file():
        return SentinelState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SentinelState()
    if not isinstance(payload, dict):
        return SentinelState()
    return SentinelState.from_dict(payload)


def save_state(path: Path, state: SentinelState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _format_duration(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


def evaluate_probe(
    state: SentinelState, classification: str, detail: str, *, now: datetime
) -> tuple[SentinelState, list[SentinelEvent]]:
    """Exactly one alert per outage (on the 3rd consecutive failure) and
    exactly one recovery notice (on the first success after an alerted
    outage); a recovery with no preceding alert stays silent."""
    now_iso = now.isoformat()
    if classification == "ok":
        events: list[SentinelEvent] = []
        if state.last_alert_at is not None:
            outage_started = state.outage_started_at or now_iso
            duration = now - datetime.fromisoformat(outage_started)
            events.append(
                SentinelEvent(
                    kind="recovery",
                    message=f"hub recovered after {_format_duration(duration)} (was {state.last_failure_class})",
                )
            )
        return SentinelState(), events

    failures = state.consecutive_failures + 1
    outage_started_at = state.outage_started_at or now_iso
    last_alert_at = state.last_alert_at
    events = []
    if failures == ALERT_AFTER_FAILURES and last_alert_at is None:
        events.append(SentinelEvent(kind="alert", message=f"hub unreachable: {classification} ({detail})"))
        last_alert_at = now_iso
    new_state = SentinelState(
        consecutive_failures=failures,
        outage_started_at=outage_started_at,
        last_alert_at=last_alert_at,
        last_failure_class=classification,
    )
    return new_state, events


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


def run_once(
    *,
    hub_url: str,
    state_path: Path,
    sinks: list[AlertSink],
    dry_run: bool,
    now: datetime | None = None,
) -> list[SentinelEvent]:
    moment = now or datetime.now(UTC)
    classification, detail = probe_hub(hub_url)
    state = load_state(state_path)
    new_state, events = evaluate_probe(state, classification, detail, now=moment)
    if not dry_run:
        save_state(state_path, new_state)
    for event in events:
        if dry_run:
            print(f"[dry-run] would send: {event.kind}: {event.message}")  # noqa: T201
        else:
            dispatch(sinks, event.message)
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Out-of-band hub liveness sentinel")
    parser.add_argument("--hub-url", default=os.environ.get("HUB_URL", "http://deskcomputer:8321"))
    parser.add_argument("--state-file", default=str(default_state_path()))
    parser.add_argument("--dry-run", action="store_true", help="probe and print, but never persist state or alert")
    args = parser.parse_args(argv)
    sinks = [] if args.dry_run else build_sinks_from_env(dict(os.environ))
    events = run_once(
        hub_url=args.hub_url,
        state_path=Path(args.state_file).expanduser(),
        sinks=sinks,
        dry_run=args.dry_run,
    )
    for event in events:
        print(f"{event.kind}: {event.message}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
