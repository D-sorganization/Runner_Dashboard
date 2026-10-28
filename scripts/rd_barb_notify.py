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
import re
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import rd_threads_envelope as _envelope  # noqa: E402

log = logging.getLogger("rd_barb.notify")

KINDS = frozenset({"fyi", "question", "alert", "update"})
DEPRECATED_KIND_ALIASES = {"fyi": "update"}
URGENCIES = frozenset({"low", "normal", "high"})
DEDUPE_WINDOW_S = 24 * 3600
_HOME = Path.home() / ".config" / "runner-dashboard"
DEFAULT_CONFIG = _HOME / "rd-barb.env"
DEFAULT_STATE = _HOME / "rd-barb-notify-state.json"
_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_TRUTHY = frozenset({"1", "true", "yes", "on"})

Sender = Callable[[str, Mapping[str, str], dict[str, Any]], None]
ThreadPoster = Callable[[str, str, str], None]


def normalize_role(role: str | None = None) -> str:
    """Return canonical persona role slug ('rd-barb' default); raise ValueError on invalid slug."""
    raw = "rd-barb" if role is None else str(role).strip()
    if not _ROLE_RE.match(raw):
        raise ValueError(f"invalid persona role {role!r}; expected lowercase slug matching ^[a-z][a-z0-9-]*$")
    return "rd-barb" if raw == "barb" else raw


def role_env_prefix(role: str | None = None) -> str:
    """Return the RD_<ROLE>_ environment variable prefix ('RD_BARB_' for rd-barb)."""
    norm = normalize_role(role)
    token = norm.upper().replace("-", "_")
    return "RD_BARB_" if norm == "rd-barb" else (f"{token}_" if token.startswith("RD_") else f"RD_{token}_")


def default_config_path(role: str = "rd-barb") -> Path:
    return DEFAULT_CONFIG if (norm := normalize_role(role)) == "rd-barb" else _HOME / f"rd-{norm}.env"


def default_state_path(role: str = "rd-barb") -> Path:
    return DEFAULT_STATE if (norm := normalize_role(role)) == "rd-barb" else _HOME / f"rd-{norm}-notify-state.json"


def _one_line(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value


def _as_shared_envelope(msg: dict[str, Any]) -> dict[str, Any]:
    """Map an rd-barb or persona message onto the shared rd_threads_envelope shape (#3072, #3112, #3295)."""
    kind = str(msg.get("kind"))
    sender = str(msg.get("sender_role") or "rd-barb")
    return _envelope.with_defaults(
        {**msg, "kind": DEPRECATED_KIND_ALIASES.get(kind, kind), "from": sender, "to": "barb"}
    )


def validate(msg: Any) -> list[str]:
    """Return every contract problem with ``msg``; an empty list means valid.

    Shape and enum rules (kind, urgency, question options, alert recommended_action,
    the required one-line ``recommendation_reason`` on questions per #3137) are
    delegated to the shared ``rd_threads_envelope`` contract (#3072) so rd-barb's
    messages, barb_cli and the MCP server validate identically (#3112). ``dedupe_key``
    is notify-specific and stays here.
    """
    if not isinstance(msg, dict):
        return ["message must be a JSON object"]
    errors: list[str] = []
    kind = msg.get("kind")
    if kind not in KINDS:
        errors.append(f"kind must be one of {sorted(KINDS)}")
    else:
        errors.extend(_envelope.validate(_as_shared_envelope(msg)))
    if not _one_line(msg.get("dedupe_key")):
        errors.append("dedupe_key must be one non-empty line")
    return errors


def load_config(env: Mapping[str, str] | None = None, *, role: str = "rd-barb") -> dict[str, str]:
    """Env wins over the git-ignored config file; missing keys stay absent."""
    env = os.environ if env is None else env
    norm = normalize_role(role)
    prefix = role_env_prefix(norm)
    cfg_key = f"{prefix}CONFIG"
    cfg: dict[str, str] = {}
    path = Path(env.get(cfg_key) or default_config_path(norm)).expanduser()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                cfg[key.strip()] = value.strip()
    except OSError:
        pass
    for key, value in env.items():  # every non-empty RD_<ROLE>_* setting (provider, launch, webhook)
        if key.startswith(prefix) and key != cfg_key and value:
            cfg[key] = value
    if norm != "rd-barb":
        for key in ("RD_BARB_WEBHOOK_URL", "RD_BARB_WEBHOOK_KEY", "RD_BARB_WEBHOOK_KEY_HEADER"):
            if env.get(key) and key not in cfg:
                cfg[key] = env[key]
    return cfg


class Notifier:
    """Validates, dedupes, batches and delivers rd-barb / persona envelopes. Never raises."""

    def __init__(
        self,
        config: Mapping[str, str],
        state_path: Path,
        sender: Sender,
        thread_poster: ThreadPoster | None = None,
        clock: Callable[[], float] = time.time,
        *,
        role: str = "rd-barb",
    ) -> None:
        self._role = normalize_role(role)
        self._prefix = role_env_prefix(self._role)
        self._cfg, self._state_path, self._send, self._post, self._now = (
            dict(config), state_path, sender, thread_poster, clock
        )

    def _with_role(self, msg: dict[str, Any]) -> dict[str, Any]:
        if self._role == "rd-barb" and "sender_role" not in msg:
            return msg
        return {**msg, "sender_role": str(msg.get("sender_role") or self._role)}

    def notify(self, msg: Any) -> dict[str, Any]:
        errors = validate(msg)
        if errors:
            log.error("rd-barb envelope rejected: %s", "; ".join(errors))
            return {"ok": False, "status": "rejected", "error": "invalid_envelope", "problems": errors}
        out = self._with_role(msg)
        state = self._load_state()
        if self._is_duplicate(state, out["dedupe_key"]):
            return {"ok": True, "status": "deduped"}
        if out["kind"] in ("fyi", "update"):
            state["digest"].append(out)
            self._mark(state, out["dedupe_key"])
            return {"ok": True, "status": "queued_for_digest"}
        result = self._deliver(out)
        if result["ok"]:
            self._mark(state, out["dedupe_key"])
        return result

    def flush(self) -> dict[str, Any]:
        state = self._load_state()
        items = state["digest"]
        if not items:
            return {"ok": True, "status": "empty"}
        digest = {
            "kind": "fyi",
            "summary": f"{self._role} digest: {len(items)} update(s)",
            "body": "\n".join(f"- {m['summary']}" for m in items),
            "urgency": "low",
            "dedupe_key": f"digest-{int(self._now())}",
            "items": items,
        }
        result = self._deliver(self._with_role(digest))
        if result["ok"]:
            state["digest"] = []
            self._save_state(state)
        return result

    def _deliver(self, msg: dict[str, Any]) -> dict[str, Any]:
        if (self._cfg.get(f"{self._prefix}SHADOW") or "").strip().lower() in _TRUTHY:
            self._mirror(msg)
            return {"ok": True, "status": "shadow"}
        url = self._cfg.get(f"{self._prefix}WEBHOOK_URL") or self._cfg.get("RD_BARB_WEBHOOK_URL")
        key = self._cfg.get(f"{self._prefix}WEBHOOK_KEY") or self._cfg.get("RD_BARB_WEBHOOK_KEY")
        if not url or not key:
            log.error("rd-barb webhook config missing; message not sent")
            return {"ok": False, "status": "not_sent", "error": "config_missing"}
        header = (
            self._cfg.get(f"{self._prefix}WEBHOOK_KEY_HEADER")
            or self._cfg.get("RD_BARB_WEBHOOK_KEY_HEADER")
            or "Authorization"
        )
        value = f"Bearer {key}" if header == "Authorization" else key
        try:
            self._send(url, {header: value, "Content-Type": "application/json"}, msg)
        except Exception as exc:  # noqa: BLE001 - fail closed, never crash rd-barb
            log.error("rd-barb webhook send failed: %s", type(exc).__name__)
            return {"ok": False, "status": "not_sent", "error": "send_failed"}
        self._mirror(msg)
        return {"ok": True, "status": "sent"}

    def _mirror(self, msg: dict[str, Any]) -> None:
        thread_id = msg.get("thread_id") or self._cfg.get(f"{self._prefix}THREAD_ID") or self._cfg.get("RD_BARB_THREAD_ID")
        if not (thread_id and self._post):
            return
        try:
            self._post(thread_id, json.dumps(msg, sort_keys=True), f"{self._role}-{msg['dedupe_key']}")
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


def _fleet_thread_poster(role: str = "rd-barb") -> ThreadPoster | None:
    norm = normalize_role(role)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "fleet"))
    try:
        from fleet_client import FleetClient  # noqa: PLC0415

        client = FleetClient(agent=norm)
    except Exception:  # noqa: BLE001 - optional thread mirror must never crash CLI startup
        return None
    return lambda tid, body, key: client.staff_message_send(tid, body, idempotency_key=key)


def _resolve_poster(role: str) -> ThreadPoster | None:
    return _fleet_thread_poster() if role == "rd-barb" else _fleet_thread_poster(role=role)


def persona_notify(
    role: str,
    msg: dict[str, Any],
    *,
    config: Mapping[str, str] | None = None,
    state_path: Path | None = None,
    sender: Sender = http_send,
    thread_poster: ThreadPoster | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Validate, stamp ``sender_role``, and deliver a persona envelope via Notifier (#3295, #3296)."""
    norm = normalize_role(role)
    if not isinstance(msg, dict):
        return {"ok": False, "status": "rejected", "error": "invalid_envelope", "problems": ["message must be a JSON object"]}
    cfg = load_config(role=norm) if config is None else config
    st = default_state_path(norm) if state_path is None else state_path
    poster = _resolve_poster(norm) if thread_poster is None and config is None else thread_poster
    return Notifier(cfg, st, sender, poster, clock=clock, role=norm).notify({**msg, "sender_role": norm})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", default="rd-barb", help="persona role slug (default: rd-barb)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    send = sub.add_parser("send")
    send.add_argument("--role", dest="sub_role", default=None)
    send.add_argument("--json", required=True, help="envelope file, or - for stdin")
    flush_p = sub.add_parser("flush")
    flush_p.add_argument("--role", dest="sub_role", default=None)
    args = parser.parse_args(argv)
    role = normalize_role(args.sub_role or args.role)
    state_env = os.environ.get(f"{role_env_prefix(role)}NOTIFY_STATE") or (
        os.environ.get("RD_BARB_NOTIFY_STATE") if role == "rd-barb" else None
    )
    state = Path(state_env or default_state_path(role)).expanduser()
    notifier = Notifier(load_config(role=role), state, http_send, _resolve_poster(role), role=role)
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
