#!/usr/bin/env python3
"""rd-barb -> Dieter direct notify path, with no Grok relay (RD #3075; barbara #11 step 3).

Questions and alerts go straight to a channel Dieter picks: Telegram (RD #3076) or the dashboard plus email.
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


CHANNELS: dict[str, Callable[[Mapping[str, str]], NotifyChannel]] = {
    "none": lambda _cfg: _FailingChannel("none", "no_channel_configured"),
}


def channel_from_config(cfg: Mapping[str, str]) -> NotifyChannel:
    """The channel named by ``RD_BARB_DIRECT_CHANNEL`` (default ``none``); an unregistered name fails closed."""
    name = (cfg.get("RD_BARB_DIRECT_CHANNEL") or "none").strip()
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
    error = _send(channel, msg)
    state = _record(health, channel.name, error, clock())
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


def _record(health: NotifyHealth, channel: str, error: str, now: float) -> str:
    try:
        health.record(ok=not error, channel=channel, error=error, now=now)
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
