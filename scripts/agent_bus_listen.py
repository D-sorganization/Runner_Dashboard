#!/usr/bin/env python3
"""Hold the agent-bus SSE inbox stream and wake a lane the moment a message lands (#3682 S2).

Stdlib only so it runs on any lane host. On every ``event: message`` it:
  * touches ``--wake-file`` (lanes sleep with ``agent_bus_wait`` style mtime checks), and
  * optionally sends SIGUSR1 to the pid in ``--pid-file`` (interrupts ``sleep``).
The last seen cursor is persisted next to the wake file so reconnects never drop messages.
The bearer token is read from ``--token-file`` and never logged.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


def _wake(wake_file: Path, pid_file: Path | None, payload: dict) -> None:
    wake_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = wake_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": time.time(), "id": payload.get("id"), "from": payload.get("from_session")}))
    os.replace(tmp, wake_file)
    if pid_file and pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), signal.SIGUSR1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass


def parse_sse(lines):  # type: ignore[no-untyped-def]
    """Yield (event, id, data) tuples from an iterable of decoded SSE lines."""
    event, eid, data = "message", None, []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield event, eid, "\n".join(data)
            event, eid, data = "message", None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "id":
            eid = value
        elif field == "data":
            data.append(value)


def register(args: argparse.Namespace, token: str) -> None:
    """(Re)register ``--session`` on the agent bus so the stream's ownership check passes."""
    body = json.dumps(
        {
            "session": args.session,
            "role": args.register_role,
            "provider": args.provider,
            "node": args.node,
            "lease_seconds": 86400,
        }
    ).encode()
    req = urllib.request.Request(
        f"{args.hub.rstrip('/')}/api/v1/agent/register",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - hub URL is operator config
        resp.read()


def run_once(args: argparse.Namespace, cursor: int) -> int:
    token = Path(args.token_file).expanduser().read_text().strip()
    if args.register_role:
        register(args, token)
    q = urllib.parse.urlencode({"session": args.session, "since_cursor": cursor})
    req = urllib.request.Request(
        f"{args.hub.rstrip('/')}/api/v1/agent/inbox/stream?{q}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/event-stream",
            "Last-Event-ID": str(cursor),
        },
    )
    cursor_file = Path(args.wake_file).with_suffix(".cursor")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - hub URL is operator config
        lines = (b.decode("utf-8", "replace") for b in resp)
        for event, eid, data in parse_sse(lines):
            if event != "message":
                continue
            if eid and eid.isdigit():
                cursor = max(cursor, int(eid))
                cursor_file.write_text(str(cursor))
            try:
                payload = json.loads(data)
            except ValueError:
                payload = {}
            _wake(Path(args.wake_file), Path(args.pid_file) if args.pid_file else None, payload)
            print(f"wake id={payload.get('id')} cursor={cursor}", flush=True)
    return cursor


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub", default=os.environ.get("HUB_URL", "http://127.0.0.1:8321"))
    p.add_argument("--token-file", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--wake-file", required=True)
    p.add_argument("--pid-file")
    p.add_argument("--register-role", help="register --session under this role before streaming")
    p.add_argument("--provider", default="cli")
    p.add_argument("--node", default=os.uname().nodename if hasattr(os, "uname") else "lane")
    p.add_argument("--once", action="store_true", help="exit after the first stream ends (tests)")
    args = p.parse_args(argv)
    cursor_file = Path(args.wake_file).with_suffix(".cursor")
    cursor = int(cursor_file.read_text()) if cursor_file.exists() and cursor_file.read_text().isdigit() else 0
    delay = 1.0
    while True:
        try:
            cursor = run_once(args, cursor)
            delay = 1.0
        except Exception as exc:  # noqa: BLE001 - keep listening through hub restarts
            print(f"stream error: {type(exc).__name__}; retry in {delay:.0f}s", file=sys.stderr, flush=True)
            if args.once:
                return 1
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
            continue
        if args.once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
