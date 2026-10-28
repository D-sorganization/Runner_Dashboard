"""barb_cli (#3072): rd-threads client over RD staff threads. ``to="*"`` is the board; ``dieter`` routes via barb.
DbC: verbs never raise, validate before I/O and never await a reply; the Idempotency-Key is the 60 s dedupe key, so
replay/409 is success and a 5xx retry never double-posts (#3024); the token is scrubbed from all output/logs."""

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_ROOT), str(_ROOT / "clients" / "fleet")]

from fleet_validators import FleetAPIError  # noqa: E402

from scripts import rd_threads_envelope as envelope  # noqa: E402

log = logging.getLogger("rd_threads.cli")
BOARD_TITLE = "rd-threads:fleet-board"
THREADS = "/api/v1/staff/threads"
RETRY_STATUSES = frozenset({0, 500, 502, 503, 504})
RETRY_DELAYS = (0.1, 0.3, 0.9)


class BarbCli:
    """Validated, idempotent rd-threads verbs over one hub transport (a ``FleetClient``)."""

    def __init__(self, hub: Any, *, agent: str, token: Any, clock: Any = time.time, sleep: Any = time.sleep) -> None:
        self._hub, self._agent, self._token, self._now, self._sleep = hub, agent, token, clock, sleep
        self._threads: dict[str, str] = {}

    def send(self, *, to: str, kind: str, summary: str, **fields: Any) -> dict[str, Any]:
        now = self._now()
        msg = envelope.seal({**fields, "kind": kind, "from": self._agent, "to": to, "summary": summary}, now)
        if problems := envelope.validate(msg):
            return {"ok": False, "error": "invalid_envelope", "problems": problems}
        try:
            tid = self._thread_for(to)
            status = self._post_once(tid, msg, envelope.dedupe_key(msg, now))
        except FleetAPIError as exc:
            return self._failure(exc)
        return {"ok": True, "status": status, "correlation_id": msg["correlation_id"], "thread_id": tid}

    def ask_barb(self, *, summary: str, options: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
        return self.send(to="barb", kind="question", summary=summary, options=options, **fields)

    def inbox(self, limit: int = 50) -> dict[str, Any]:
        unread = lambda: self._hub.request("GET", THREADS, params={"unread": True, "limit": limit})  # noqa: E731
        return self._read(lambda: {"items": [e for t in _rows(unread(), "threads") for e in self._mine(str(t["id"]))]})

    def peers(self) -> dict[str, Any]:
        return self._read(lambda: {"peers": _rows(self._hub.request("GET", "/api/coordination/sessions"), "sessions")})

    def _read(self, read: Any) -> dict[str, Any]:
        try:
            return {"ok": True, **read()}
        except FleetAPIError as exc:  # 403 fails closed: never retried with any other token (#2942)
            return self._failure(exc, scoped=True)

    def _thread_for(self, to: str) -> str:
        role = "barb" if to in ("barb", "dieter") else to
        title = BOARD_TITLE if to == "*" else f"rd-threads:{role}"
        if title not in self._threads:  # the create replays per title under its Idempotency-Key (#1795)
            body = {"role": "auto", "kind": "group", "title": title} if to == "*" else {"role": role, "title": title}
            created = self._hub.request("POST", THREADS, body=body, headers={"Idempotency-Key": title})
            self._threads[title] = str(created["id"])
        return self._threads[title]

    def _post_once(self, tid: str, msg: dict[str, Any], key: str) -> str:
        text = f"[{msg['kind']}] {msg['summary']}\n\n{msg['body']}".rstrip()
        body, path = {"body": text, "kind": "text", "meta": {"rd_threads": msg}}, f"{THREADS}/{tid}/messages"
        for delay in (*RETRY_DELAYS, None):
            try:
                res = self._hub.request("POST", path, body=body, headers={"Idempotency-Key": key})
                return "replayed" if isinstance(res, dict) and res.get("replay") else "sent"
            except FleetAPIError as exc:
                if exc.status == 409:
                    return "replayed"  # this key was already accepted: success, never a second post
                if exc.status not in RETRY_STATUSES or delay is None:
                    raise
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _mine(self, tid: str) -> list[dict[str, Any]]:
        msgs = _rows(self._hub.request("GET", f"{THREADS}/{tid}", params={"since_seq": 0}), "messages")
        envs = [e for e in ((m.get("meta") or {}).get("rd_threads") for m in msgs) if isinstance(e, dict)]
        return [e for e in envs if e.get("to") in (self._agent, "*") and e.get("from") != self._agent]

    def _failure(self, exc: FleetAPIError, *, scoped: bool = False) -> dict[str, Any]:
        if scoped and exc.status == 403:
            log.error("rd-threads: principal lacks fleet.read; not retrying with any other token")
            return {"ok": False, "error": "missing_scope", "required_scope": "fleet.read"}
        message = str(exc).replace(self._token, "[redacted]") if self._token else str(exc)
        log.error("rd-threads hub call failed: %s", message)
        return {"ok": False, "error": f"http_{exc.status}", "status": exc.status, "message": message}


def _rows(res: Any, key: str) -> list[dict[str, Any]]:
    rows = (res.get(key) or res.get("items") or []) if isinstance(res, dict) else (res or [])
    return [r for r in rows if isinstance(r, dict)]


def dispatch(cli: BarbCli, verb: str, args: Any) -> dict[str, Any]:
    """Run one verb with JSON-object args; the single entry point for the CLI and the MCP server."""
    try:
        return getattr(cli, verb.replace("-", "_"))(**(args if isinstance(args, dict) else {}))
    except TypeError as exc:
        return {"ok": False, "error": "invalid_arguments", "message": str(exc)}


def build_cli() -> BarbCli:
    from fleet_client import FleetClient

    token = os.environ.get("FLEET_API_TOKEN")
    if not token:
        path = Path(os.environ.get("RD_THREADS_TOKEN_FILE") or "~/.config/runner-dashboard/fleet-token").expanduser()
        token = path.read_text(encoding="utf-8").strip() if path.is_file() else None
    agent = os.environ.get("FLEET_AGENT") or "agent"
    return BarbCli(FleetClient(token=token or None, agent=agent), agent=agent, token=token or None)


def _one_line_reason(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value


def main(argv: Sequence[str] | None = None) -> int:
    """``barb_cli.py {send|ask-barb|inbox|peers} ['{"to": ..., "kind": ..., "summary": ...}'] [--reason LINE]``."""
    parser = argparse.ArgumentParser(description="rd-threads client (args are one JSON object)")
    parser.add_argument("verb", choices=("send", "ask-barb", "inbox", "peers"))
    parser.add_argument("args", nargs="?", default="{}", help="JSON object of verb arguments")
    parser.add_argument("--reason", default="", help="one-line recommendation_reason (required for ask-barb)")
    ns = parser.parse_args(argv)
    payload = json.loads(ns.args)
    if ns.verb == "ask-barb":
        reason = (ns.reason or "").strip() or str(payload.get("recommendation_reason") or "").strip()
        if not _one_line_reason(reason):
            out = {
                "ok": False,
                "error": "missing_reason",
                "message": "ask-barb requires a one-line --reason (or recommendation_reason in JSON)",
            }
            print(json.dumps(out, sort_keys=True, default=str))
            return 1
        payload["recommendation_reason"] = reason
    print(json.dumps(result := dispatch(build_cli(), ns.verb, payload), sort_keys=True, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
