#!/usr/bin/env python3
"""rd-barb -> Grok Barb -> Dieter message path (docs/agents/rd-barb.md).

rd-barb never messages Dieter directly. She posts one JSON envelope to a Grok Bot
routine webhook (Grok Barb relays it verbatim) and mirrors it into her Staff
thread, where Grok Barb posts Dieter's answer back.

Contract (DbC): ``notify`` never raises. Every outcome is a dict with ``ok`` and
``status``; a bad envelope or missing config fails closed with ``ok: False`` and an
``error`` the caller can act on. Questions must carry exactly one recommended
option plus a one-line ``recommendation_reason``; alerts must carry a
``recommended_action`` (Dieter, t776u).

  send  --json FILE|-     validate, dedupe, then send (fyi is queued for the digest)
  flush                   send queued fyi messages as one digest

Config (env, or KEY=VALUE lines in RD_BARB_CONFIG, default
~/.config/runner-dashboard/rd-barb.env; never committed): RD_BARB_WEBHOOK_URL,
RD_BARB_WEBHOOK_KEY, optional RD_BARB_WEBHOOK_KEY_HEADER, RD_BARB_THREAD_ID.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger("rd_barb.notify")

KINDS = frozenset({"fyi", "question", "alert"})
URGENCIES = frozenset({"low", "normal", "high"})
DEDUPE_WINDOW_S = 24 * 3600
_HOME = Path.home() / ".config" / "runner-dashboard"
DEFAULT_CONFIG = _HOME / "rd-barb.env"
DEFAULT_STATE = _HOME / "rd-barb-notify-state.json"

Sender = Callable[[str, Mapping[str, str], dict[str, Any]], None]
ThreadPoster = Callable[[str, str, str], None]


def _one_line(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value


def validate(msg: Any) -> list[str]:
    """Return every contract problem with ``msg``; an empty list means valid."""
    if not isinstance(msg, dict):
        return ["message must be a JSON object"]
    errors: list[str] = []
    kind = msg.get("kind")
    if kind not in KINDS:
        errors.append(f"kind must be one of {sorted(KINDS)}")
    if not _one_line(msg.get("summary")):
        errors.append("summary must be one non-empty line")
    if not isinstance(msg.get("body", ""), str):
        errors.append("body must be a string")
    if msg.get("urgency") not in URGENCIES:
        errors.append(f"urgency must be one of {sorted(URGENCIES)}")
    if not _one_line(msg.get("dedupe_key")):
        errors.append("dedupe_key must be one non-empty line")
    if kind == "question":
        errors.extend(_question_errors(msg))
    if kind == "alert" and not _one_line(msg.get("recommended_action")):
        errors.append("alert requires a one-line recommended_action")
    return errors


def _question_errors(msg: dict[str, Any]) -> list[str]:
    options = msg.get("options")
    if not isinstance(options, list) or len(options) < 2:
        return ["question requires options: a list of at least 2 {label, recommended?}"]
    errors = [
        f"options[{i}].label must be one non-empty line"
        for i, o in enumerate(options)
        if not (isinstance(o, dict) and _one_line(o.get("label")))
    ]
    recommended = sum(1 for o in options if isinstance(o, dict) and o.get("recommended") is True)
    if recommended != 1:
        errors.append(f"question needs exactly one option with recommended: true (found {recommended})")
    if not _one_line(msg.get("recommendation_reason")):
        errors.append("question requires a one-line recommendation_reason")
    return errors


def load_config(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Env wins over the git-ignored config file; missing keys stay absent."""
    env = os.environ if env is None else env
    cfg: dict[str, str] = {}
    path = Path(env.get("RD_BARB_CONFIG") or DEFAULT_CONFIG).expanduser()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                cfg[key.strip()] = value.strip()
    except OSError:
        pass
    for key in ("RD_BARB_WEBHOOK_URL", "RD_BARB_WEBHOOK_KEY", "RD_BARB_WEBHOOK_KEY_HEADER", "RD_BARB_THREAD_ID"):
        if env.get(key):
            cfg[key] = env[key]
    return cfg


class Notifier:
    """Validates, dedupes, batches and delivers rd-barb envelopes. Never raises."""

    def __init__(
        self,
        config: Mapping[str, str],
        state_path: Path,
        sender: Sender,
        thread_poster: ThreadPoster | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg, self._state_path, self._send, self._post, self._now = (
            dict(config),
            state_path,
            sender,
            thread_poster,
            clock,
        )

    def notify(self, msg: Any) -> dict[str, Any]:
        errors = validate(msg)
        if errors:
            log.error("rd-barb envelope rejected: %s", "; ".join(errors))
            return {"ok": False, "status": "rejected", "error": "invalid_envelope", "problems": errors}
        state = self._load_state()
        if self._is_duplicate(state, msg["dedupe_key"]):
            return {"ok": True, "status": "deduped"}
        if msg["kind"] == "fyi":
            state["digest"].append(msg)
            self._mark(state, msg["dedupe_key"])
            return {"ok": True, "status": "queued_for_digest"}
        result = self._deliver(msg)
        if result["ok"]:
            self._mark(state, msg["dedupe_key"])
        return result

    def flush(self) -> dict[str, Any]:
        state = self._load_state()
        items = state["digest"]
        if not items:
            return {"ok": True, "status": "empty"}
        digest = {
            "kind": "fyi",
            "summary": f"rd-barb digest: {len(items)} update(s)",
            "body": "\n".join(f"- {m['summary']}" for m in items),
            "urgency": "low",
            "dedupe_key": f"digest-{int(self._now())}",
            "items": items,
        }
        result = self._deliver(digest)
        if result["ok"]:
            state["digest"] = []
            self._save_state(state)
        return result

    def _deliver(self, msg: dict[str, Any]) -> dict[str, Any]:
        url, key = self._cfg.get("RD_BARB_WEBHOOK_URL"), self._cfg.get("RD_BARB_WEBHOOK_KEY")
        if not url or not key:
            log.error("rd-barb webhook config missing; message not sent")
            return {"ok": False, "status": "not_sent", "error": "config_missing"}
        header = self._cfg.get("RD_BARB_WEBHOOK_KEY_HEADER") or "Authorization"
        value = f"Bearer {key}" if header == "Authorization" else key
        try:
            self._send(url, {header: value, "Content-Type": "application/json"}, msg)
        except Exception as exc:  # noqa: BLE001 - fail closed, never crash the role
            log.error("rd-barb webhook send failed: %s", type(exc).__name__)
            return {"ok": False, "status": "not_sent", "error": "send_failed"}
        self._mirror(msg)
        return {"ok": True, "status": "sent"}

    def _mirror(self, msg: dict[str, Any]) -> None:
        thread_id = msg.get("thread_id") or self._cfg.get("RD_BARB_THREAD_ID")
        if not (thread_id and self._post):
            return
        try:
            self._post(thread_id, json.dumps(msg, sort_keys=True), f"rd-barb-{msg['dedupe_key']}")
        except Exception as exc:  # noqa: BLE001 - the webhook already delivered
            log.warning("rd-barb thread mirror failed: %s", type(exc).__name__)

    def _is_duplicate(self, state: dict[str, Any], key: str) -> bool:
        seen = state["seen"].get(key)
        return seen is not None and self._now() - float(seen) < DEDUPE_WINDOW_S

    def _mark(self, state: dict[str, Any], key: str) -> None:
        state["seen"][key] = self._now()
        self._save_state(state)

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        cutoff = self._now() - DEDUPE_WINDOW_S
        seen = {k: v for k, v in (data.get("seen") or {}).items() if isinstance(v, (int, float)) and v >= cutoff}
        return {"seen": seen, "digest": list(data.get("digest") or [])}

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self._state_path)


def http_send(url: str, headers: Mapping[str, str], payload: dict[str, Any]) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=dict(headers), method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - operator-configured URL
        if resp.status >= 300:
            raise RuntimeError(f"webhook HTTP {resp.status}")


def _fleet_thread_poster() -> ThreadPoster | None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "fleet"))
    try:
        from fleet_client import FleetClient  # noqa: PLC0415
    except ImportError:
        return None
    client = FleetClient(agent="rd-barb")
    return lambda tid, body, key: client.staff_message_send(tid, body, idempotency_key=key)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    send = sub.add_parser("send")
    send.add_argument("--json", required=True, help="envelope file, or - for stdin")
    sub.add_parser("flush")
    args = parser.parse_args(argv)
    state = Path(os.environ.get("RD_BARB_NOTIFY_STATE") or DEFAULT_STATE).expanduser()
    notifier = Notifier(load_config(), state, http_send, _fleet_thread_poster())
    if args.cmd == "flush":
        result = notifier.flush()
    else:
        raw = sys.stdin.read() if args.json == "-" else Path(args.json).read_text(encoding="utf-8")
        try:
            result = notifier.notify(json.loads(raw))
        except ValueError:
            result = {"ok": False, "status": "rejected", "error": "invalid_json"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
