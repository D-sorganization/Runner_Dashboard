#!/usr/bin/env python3
"""Out-of-band hub liveness sentinel (#3050): probe classification and the
alert/cooldown/recovery state machine.

Runs on a second machine (Brick) and tells Barb/Dieter once per hub outage
and once on recovery, so dispatchers stop sending work into a dead hub.
Pure stdlib and self-contained like every other ``deploy/*.py`` entrypoint
(see ``node-auto-deploy.py``): it must run without the hub's own backend
venv installed, since the whole point is that the hub cannot report its
own outage from inside its own process.

The alert-sink port and ``dispatch()`` fan-out live in
``hub_sentinel_alerts.py``; the CLI entrypoint that wires the two together
lands in a later slice of #3050. This module is the probe + state-machine
core they both build on.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
