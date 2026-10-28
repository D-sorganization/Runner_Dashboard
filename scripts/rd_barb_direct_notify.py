#!/usr/bin/env python3
"""rd-barb -> Dieter direct notify path, with no Grok relay (RD #3075; barbara #11 step 3).

Questions and alerts go straight to the channel(s) Dieter picked: the dashboard inbox plus email (``dashboard,email``).
Until he picks one, the only channel is ``none``, which fails every delivery, so non-delivery shows on
``GET /api/v1/staff/rd-barb/notify-status`` (``failing`` at once, ``degraded`` after 5 minutes) instead of
being silent. A channel is one adapter registered in ``CHANNELS``; nothing else changes. The fyi digest
stays on ``rd_barb_notify.py``.

  send --json FILE|-   validate with rd_barb_notify.validate, then deliver on RD_BARB_DIRECT_CHANNEL
  status               print the delivery health

Contract (DbC): ``deliver`` never raises. Every attempt is recorded in the health file as an error code,
never message text, addresses or keys.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from staff.rd_barb_inbox import DashboardInbox, InboxRefused, default_inbox_path  # noqa: E402
from staff.rd_barb_notify_health import NotifyHealth, default_health_path  # noqa: E402

log = logging.getLogger("rd_barb.direct_notify")

DIRECT_KINDS = frozenset({"question", "alert"})


class ChannelError(RuntimeError):
    """A channel could not deliver; ``code`` is the short reason recorded in the health file."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class NotifyChannel(Protocol):
    name: str

    def send(self, msg: dict[str, Any]) -> None:
        """Deliver ``msg`` or raise."""


class _FailingChannel:
    """Fails every delivery with one code: no channel picked yet, or a name nobody registered."""

    def __init__(self, name: str, code: str) -> None:
        self.name, self._code = name, code

    def send(self, msg: dict[str, Any]) -> None:
        raise ChannelError(self._code)


class DashboardChannel:
    """Appends the envelope to the dashboard inbox (``GET /api/v1/staff/rd-barb/inbox``)."""

    name = "dashboard"

    def __init__(self, inbox: DashboardInbox) -> None:
        self._inbox = inbox

    def send(self, msg: dict[str, Any]) -> None:
        try:
            self._inbox.add(msg)
        except InboxRefused as exc:
            raise ChannelError("secret_refused") from exc


class FanOutChannel:
    """Delivers to every member; the delivery counts only if all of them succeed.

    ``send_each`` returns ``{member name: error code or ""}`` so health can record each member apart.
    """

    def __init__(self, members: Sequence[NotifyChannel]) -> None:
        if not members:
            raise ValueError("a fan-out needs at least one channel")
        self._members = list(members)
        self.name = "+".join(m.name for m in members)

    def send_each(self, msg: dict[str, Any]) -> dict[str, str]:
        return {m.name: _send(m, msg) for m in self._members}

    def send(self, msg: dict[str, Any]) -> None:
        if failed := _failures(self.send_each(msg)):
            raise ChannelError(failed)


CHANNELS: dict[str, Callable[[Mapping[str, str]], NotifyChannel]] = {
    "none": lambda _cfg: _FailingChannel("none", "no_channel_configured"),
    "dashboard": lambda _cfg: DashboardChannel(DashboardInbox(default_inbox_path())),
}


def channel_from_config(cfg: Mapping[str, str]) -> NotifyChannel:
    """The channel(s) named by ``RD_BARB_DIRECT_CHANNEL``, comma separated (default ``none``).

    An unregistered name fails closed: that member (recorded as ``unknown``) fails every delivery.
    """
    raw = cfg.get("RD_BARB_DIRECT_CHANNEL") or "none"
    names = list(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip())) or ["none"]
    members = [_member(name, cfg) for name in names]
    return members[0] if len(members) == 1 else FanOutChannel(members)


def _member(name: str, cfg: Mapping[str, str]) -> NotifyChannel:
    factory = CHANNELS.get(name)
    return factory(cfg) if factory else _FailingChannel("unknown", "unknown_channel")


def deliver(
    msg: Any, channel: NotifyChannel, health: NotifyHealth, *, clock: Callable[[], float] = time.time
) -> dict[str, Any]:
    """Validate, send once, record the attempt. Never raises; ``ok`` is True only when the channel delivered."""
    from scripts.rd_barb_notify import validate  # noqa: PLC0415 - shared envelope contract (DRY)

    problems = validate(msg)
    if problems:
        return {"ok": False, "status": "rejected", "error": "invalid_envelope", "problems": problems}
    if msg["kind"] not in DIRECT_KINDS:
        return {"ok": False, "status": "rejected", "error": "fyi_uses_digest"}
    now = clock()
    outcomes = _send_each(channel, msg)
    error = _failures(outcomes) if len(outcomes) > 1 else next(iter(outcomes.values()))
    state = _record(health, channel.name, error, now, outcomes if len(outcomes) > 1 else {})
    if error:
        log.error("rd-barb direct notify not sent: %s", error)
        return {"ok": False, "status": "not_sent", "error": error, "health": state}
    return {"ok": True, "status": "sent", "health": state}


def _send(channel: NotifyChannel, msg: dict[str, Any]) -> str:
    try:
        channel.send(msg)
    except ChannelError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 - fail closed and visible, never crash the role
        return f"send_failed:{type(exc).__name__}"
    return ""


def _send_each(channel: NotifyChannel, msg: dict[str, Any]) -> dict[str, str]:
    """Per-member outcomes for a fan-out; a plain channel is one entry."""
    each = getattr(channel, "send_each", None)
    return each(msg) if callable(each) else {channel.name: _send(channel, msg)}


def _failures(outcomes: Mapping[str, str]) -> str:
    """``name:code`` of each failed member, comma joined; empty when every member delivered."""
    return ",".join(f"{name}:{code}" for name, code in outcomes.items() if code)


def _record(health: NotifyHealth, channel: str, error: str, now: float, members: Mapping[str, str]) -> str:
    try:
        health.record(ok=not error, channel=channel, error=error, now=now)
        for name, code in members.items():
            health.record_channel(name, ok=not code, error=code, now=now)
        return str(health.status(now=now)["state"])
    except OSError as exc:
        log.error("rd-barb notify health not recorded: %s", type(exc).__name__)
        return "unrecorded"


def main(argv: Sequence[str] | None = None) -> int:
    from scripts.rd_barb_notify import load_config  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("send").add_argument("--json", required=True, help="envelope file, or - for stdin")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    health = NotifyHealth(default_health_path())
    if args.cmd == "status":
        print(json.dumps(health.status(now=time.time()), sort_keys=True))
        return 0
    raw = sys.stdin.read() if args.json == "-" else Path(args.json).read_text(encoding="utf-8")
    try:
        result = deliver(json.loads(raw), channel_from_config(load_config()), health)
    except ValueError:
        result = {"ok": False, "status": "rejected", "error": "invalid_json"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
