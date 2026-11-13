"""rd-barb slice 8b (#3805): Telegram inbound reply mapping (Path B ``getUpdates`` -> answer routing).

Long-polls ``getUpdates``, accepts only Dieter's allowlisted chat id, extracts a correlation id and an
approve/deny decision from a reply (quoted ``Ref:`` text, or ``callback_data`` of
``"<decision>:<correlation_id>"``), and routes a match through :func:`staff.rd_barb_relay.route_answer`.
An unmatched, unknown or expired correlation id becomes a plain note in the dashboard inbox instead of
being dropped. :func:`scripts.rd_barb_notify_telegram.select_path` decides which path owns the one
``getUpdates`` slot; this poller is inert outside Path B (Bot API), or while ``RD_BARB_TELEGRAM`` is off.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.conversations import ConversationStore
from staff.rd_barb_inbox import DashboardInbox
from staff.rd_barb_ledger import DECISIONS, DecisionLedger
from staff.rd_barb_memory import rd_barb_state_dir
from staff.rd_barb_provider_state import active_provider_payload
from staff.rd_barb_relay import CorrelationRejectedError, route_answer

from scripts.rd_barb_notify_telegram import select_path

log = logging.getLogger("dashboard.scripts.rd_barb_telegram_inbound")

LONG_POLL_TIMEOUT_S = 25
HTTP_TIMEOUT_S = 10.0
_REF_RE = re.compile(r"Ref:\s*(\S+)")
_DECISION_RE = re.compile(r"\b(approve|deny)\b", re.IGNORECASE)

HttpGet = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
ProviderPayload = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True)
class ParsedReply:
    correlation_id: str
    decision: str


def parse_update(update: Mapping[str, Any]) -> ParsedReply | None:
    """Extract (correlation_id, decision) from one Telegram Update; ``None`` if either is absent (#3805)."""
    if callback := update.get("callback_query"):
        decision, _, cid = str(callback.get("data") or "").partition(":")
        decision, cid = decision.strip().lower(), cid.strip()
        return ParsedReply(cid, decision) if decision in DECISIONS and cid else None
    msg = update.get("message") or {}
    text = str(msg.get("text") or "")
    decision_match = _DECISION_RE.search(text)
    if not decision_match:
        return None
    reply_text = str((msg.get("reply_to_message") or {}).get("text") or "")
    ref_match = _REF_RE.search(reply_text) or _REF_RE.search(text)
    if not ref_match:
        return None
    return ParsedReply(ref_match.group(1), decision_match.group(1).lower())


def _chat_id(update: Mapping[str, Any]) -> str:
    message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    return str((message.get("chat") or {}).get("id") or "")


def _getupdates(token: str, offset: int | None, *, http_get: HttpGet | None = None) -> Mapping[str, Any]:
    params: dict[str, Any] = {
        "timeout": LONG_POLL_TIMEOUT_S,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    if http_get is not None:
        return http_get(url, params)
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}")  # noqa: S310 - fixed https host, never user-controlled
    with urllib.request.urlopen(req, timeout=LONG_POLL_TIMEOUT_S + HTTP_TIMEOUT_S) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def default_offset_path() -> Path:
    return rd_barb_state_dir() / "telegram_inbound_offset.json"


def load_offset(path: Path) -> int | None:
    try:
        offset = json.loads(path.read_text(encoding="utf-8")).get("offset")
    except (OSError, ValueError):
        return None
    return offset if isinstance(offset, int) else None


def save_offset(path: Path, offset: int | None) -> None:
    if offset is not None:
        path.write_text(json.dumps({"offset": offset}), encoding="utf-8")


class TelegramInboundPoller:
    """Path B inbound: map one Telegram reply to its rd-barb correlation id, or note it unmatched (#3805)."""

    def __init__(
        self,
        cfg: Mapping[str, str],
        *,
        environ: Mapping[str, str],
        store: ConversationStore,
        ledger: DecisionLedger,
        inbox: DashboardInbox,
        http_get: HttpGet | None = None,
        clock: Callable[[], float] = time.time,
        provider_payload: ProviderPayload = active_provider_payload,
    ) -> None:
        self._cfg, self._environ = dict(cfg), environ
        self._store, self._ledger, self._inbox = store, ledger, inbox
        self._http_get, self._clock, self._provider_payload = http_get, clock, provider_payload

    def _active(self) -> bool:
        if (self._cfg.get("RD_BARB_TELEGRAM") or "off") != "on":
            return False
        provider = str(self._provider_payload().get("provider") or "")
        channels_enabled = (self._cfg.get("RD_BARB_TELEGRAM_CHANNELS_ENABLED") or "off") == "on"
        return select_path(provider, channels_enabled=channels_enabled) == "bot_api"

    def poll_once(self, offset: int | None = None) -> dict[str, Any]:
        """One ``getUpdates`` long-poll cycle; routes or notes each allowlisted update (#3805)."""
        if not self._active():
            return {"ok": False, "reason": "inactive", "next_offset": offset}
        token = self._environ.get(self._cfg.get("RD_BARB_TELEGRAM_BOT_TOKEN_ENV") or "TELEGRAM_BOT_TOKEN", "")
        allowed_chat = str(self._cfg.get("RD_BARB_TELEGRAM_CHAT_ID") or "")
        if not token or not allowed_chat:
            return {"ok": False, "reason": "telegram_not_configured", "next_offset": offset}
        try:
            payload = _getupdates(token, offset, http_get=self._http_get)
        except (urllib.error.URLError, TimeoutError, OSError):
            return {"ok": False, "reason": "telegram_connect_failed", "next_offset": offset}
        routed = noted = dropped = 0
        next_offset = offset
        for update in payload.get("result") or []:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = update_id + 1
            if _chat_id(update) != allowed_chat:
                dropped += 1
                continue
            parsed = parse_update(update)
            if parsed is None:
                dropped += 1
                continue
            if self._route(parsed):
                routed += 1
            else:
                noted += 1
        return {"ok": True, "routed": routed, "noted": noted, "dropped": dropped, "next_offset": next_offset}

    def _route(self, parsed: ParsedReply) -> bool:
        try:
            route_answer(self._store, self._ledger, correlation_id=parsed.correlation_id, decision=parsed.decision)
        except CorrelationRejectedError as exc:
            self._inbox.add(
                {
                    "kind": "update",
                    "urgency": "low",
                    "summary": f"Telegram reply ({parsed.decision}) didn't match a pending question: {exc}",
                    "dedupe_key": f"telegram-unmatched:{parsed.correlation_id}:{parsed.decision}",
                }
            )
            return False
        return True


def run_loop(
    poller: TelegramInboundPoller,
    *,
    offset_path: Path,
    max_iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll forever (or ``max_iterations`` times), persisting the offset across long-poll cycles (#3805)."""
    offset = load_offset(offset_path)
    i = 0
    while max_iterations is None or i < max_iterations:
        result = poller.poll_once(offset)
        offset = result.get("next_offset", offset)
        save_offset(offset_path, offset)
        if result.get("reason") == "inactive":
            sleep(60.0)
        i += 1
