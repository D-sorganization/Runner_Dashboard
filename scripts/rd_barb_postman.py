#!/usr/bin/env python3
"""rd-barb slice 5 (#3073): postman leader (manual promotion per ADR 0008), SSE/ETag, wake & heartbeat."""

from __future__ import annotations

import argparse
import datetime as _dt_mod
import json
import logging
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from coordination.postman import PostmanState  # noqa: E402
from coordination.relay_guard import MAX_HOPS, DeadLetter, RelayGuard, RelayRejected  # noqa: E402
from staff.barb_continuity import BarbContinuityStore  # noqa: E402
from staff.barb_lease_fence import BarbClaim  # noqa: E402
from staff.barb_node_leader import renew_leadership  # noqa: E402
from staff.redaction import redact_sensitive_content  # noqa: E402
from staff.watch_tick import ETagCache, GitHubProbePort, ProbeResponse  # noqa: E402

from scripts import postman_provider_caps as _caps  # noqa: E402

log = logging.getLogger("rd_barb.postman")
UTC = _dt_mod.UTC
STREAM_LATENCY_BUDGET_S, BOARD_POLL_BUDGET_S, HEARTBEAT_INTERVAL_S = 5.0, 30.0, 600.0
BOARD_PROBE_KEY, DEFAULT_BOARD_ISSUE = "board:coordination", 2376
CLAUDE_SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"
CURSOR_HOOK_EVENTS = ("beforeSubmitPrompt", "UserPromptSubmit", "Stop")
WakeDecision = Literal["wake", "next_turn"]
DeliveryMode = Literal["push", "hook", "poll"]


class StreamSource(Protocol):
    def drain(self) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class ProviderRoute:
    provider: str
    mode: DeliveryMode
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class TickReport:
    dry_run: bool
    fenced: bool
    planned_ids: tuple[str, ...]
    delivered: tuple[dict[str, Any], ...]
    woken: tuple[str, ...]
    dropped_ids: tuple[str, ...]
    board_not_modified: bool
    heartbeat_sent: bool
    stream_latency_budget_s: float = STREAM_LATENCY_BUDGET_S
    board_poll_budget_s: float = BOARD_POLL_BUDGET_S


def classify_wake(msg: Mapping[str, Any], *, target_agent: str) -> WakeDecision:
    """Wake only on a directed message or ``@<target_agent>`` mention; broadcasts wait for next turn."""
    agent = target_agent.strip().lower()
    if not agent:
        raise ValueError("target_agent must be non-empty")
    to = str(msg.get("to") or "").strip().lower()
    if to and to != "*" and (to == agent or (agent in ("barb", "rd-barb") and to in ("barb", "rd-barb"))):
        return "wake"
    text = f"{msg.get('summary', '')} {msg.get('body', '')}"
    tokens = {agent} | ({"barb", "rd-barb"} if agent in ("barb", "rd-barb") else set())
    pattern = re.compile(r"(?<!\w)@(" + "|".join(re.escape(t) for t in sorted(tokens)) + r")\b", re.IGNORECASE)
    return "wake" if pattern.search(text) else "next_turn"


def _probe_cursor(cursor_dir: Path | None) -> _caps.ProviderCaps:
    base = cursor_dir if cursor_dir is not None else Path.home() / ".cursor"
    hooked = _caps._hook_commands(_caps._read_json(base / "hooks.json"), *CURSOR_HOOK_EVENTS)
    if hooked:
        return _caps.ProviderCaps("hook", tuple(f"cursor/hooks.json:{h}" for h in hooked))
    return _caps.ProviderCaps("poll", ("no cursor hooks configured",))


def detect_provider_caps(provider: str, **kw: Any) -> ProviderRoute:
    """Resolve how ``provider`` receives postman traffic (push vs hook vs poll)."""
    env_arg = kw.get("env")
    mapping: Mapping[str, str] = env_arg if isinstance(env_arg, Mapping) else os.environ
    norm = provider.strip().lower()
    if norm in ("claude", "claude_code", "claude-code"):
        if (mapping.get(CLAUDE_SOCKET_ENV) or "").strip():
            return ProviderRoute(norm, "push", (f"env:{CLAUDE_SOCKET_ENV} is set",))
        caps = _caps.probe_claude(mapping, _caps._claude_config_dir(mapping, kw.get("claude_config_dir")))
    elif norm in ("codex", "codex-cli"):
        caps = _caps.probe_codex(_caps._codex_home(mapping, kw.get("codex_home")))
    elif norm in ("antigravity", "agy", "gemini"):
        caps = _caps.probe_agy(_caps._agy_home(mapping))
    elif norm in ("cursor", "cursor-agent", "cursor_agent"):
        caps = _probe_cursor(kw.get("cursor_dir"))
    else:
        raise ValueError(f"unsupported provider: {provider!r}")
    return ProviderRoute(norm, cast(DeliveryMode, caps.delivery), caps.evidence)


def route_provider_delivery(provider: str, envelope: Mapping[str, Any], **kw: Any) -> ProviderRoute:
    """Deliver ``envelope`` through the highest-tier transport detected for ``provider``."""
    route = detect_provider_caps(provider, **kw)
    sink = {"push": kw.get("push_sender"), "hook": kw.get("hook_injector"), "poll": kw.get("poll_spooler")}[route.mode]
    if sink is not None:
        sink(envelope)
    return route


class PostmanLeader:
    """Fenced single-leader postman (manual promotion only per ADR 0008)."""

    def __init__(self, **kw: Any) -> None:
        self._continuity: BarbContinuityStore = kw["continuity"]
        self._claim: BarbClaim = kw["claim"]
        self._state_path: Path = kw["state_path"]
        self._stream: StreamSource = kw["stream"]
        self._board_probe: GitHubProbePort = kw["board_probe"]
        self._etag_cache: ETagCache = kw["etag_cache"]
        self._webhook: Callable[[dict[str, Any]], dict[str, Any]] = kw["webhook"]
        self._board_fallback: Callable[[str], None] = kw["board_fallback"]
        self._clock: Callable[[], datetime] = kw.get("clock") or (lambda: datetime.now(tz=UTC))
        self.state = PostmanState.load(self._state_path, default_board_issue=DEFAULT_BOARD_ISSUE)
        self.guard = RelayGuard(roster=kw["roster"])
        self._last_heartbeat_at: float | None = None
        self._in_outage = self._outage_fallback_posted = False
        self._pending_relays: list[dict[str, Any]] = []
        self._relayed_keys: set[str] = set()

    def dead_letters(self) -> tuple[DeadLetter, ...]:
        return tuple(self.guard.dead_letters())

    def relay(self, msg: Mapping[str, Any]) -> dict[str, Any]:
        """Relay a message or alert to the Grok webhook; queue in order on outage without duplicates."""
        item = dict(msg)
        key = str(item.get("dedupe_key") or item.get("id") or "").strip()
        if key and key in self._relayed_keys:
            return {"ok": True, "status": "deduped"}
        if not self._in_outage and self._webhook(item).get("ok"):
            if key:
                self._relayed_keys.add(key)
            return {"ok": True, "status": "sent"}
        self._enter_outage()
        if not key or not any(str(q.get("dedupe_key") or q.get("id") or "") == key for q in self._pending_relays):
            self._pending_relays.append(item)
        return {"ok": False, "status": "queued"}

    def _enter_outage(self) -> None:
        self._in_outage = True
        if not self._outage_fallback_posted:
            self._outage_fallback_posted = True
            self._post_board_status("[rd-barb heartbeat] webhook unreachable (outage fallback); queuing relays.")

    def _flush_pending_relays(self) -> None:
        remaining: list[dict[str, Any]] = []
        for idx, item in enumerate(self._pending_relays):
            key = str(item.get("dedupe_key") or item.get("id") or "").strip()
            if key and key in self._relayed_keys:
                continue
            if not self._webhook(item).get("ok"):
                remaining.extend(self._pending_relays[idx:])
                self._in_outage = True
                break
            if key:
                self._relayed_keys.add(key)
        self._pending_relays = remaining

    def tick(self, *, dry_run: bool = False) -> TickReport:
        """Run one fenced leader pass. Raises :class:`StaleBarbLease` if fenced off."""
        now = self._clock()
        with self._continuity.fence(self._claim, now=now):
            if not dry_run:
                renew_leadership(self._continuity, node=self._claim.caller_id, epoch=self._claim.epoch, now=now)
            raw_events, not_modified = self._collect_candidates(dry_run=dry_run)
            p, d, w, dr = self._process_events(raw_events, dry_run=dry_run)
            hb_sent = False if dry_run else self._maybe_heartbeat(now.timestamp())
            if not dry_run and d:
                self.state.save(self._state_path)
            return TickReport(dry_run, False, tuple(p), tuple(d), tuple(w), tuple(dr), not_modified, hb_sent)

    def _collect_candidates(self, *, dry_run: bool) -> tuple[list[dict[str, Any]], bool]:
        items: list[dict[str, Any]] = [e if isinstance(e, dict) else dict(e) for e in self._stream.drain()]
        resp: ProbeResponse = self._board_probe.get(BOARD_PROBE_KEY, etag=self._etag_cache.etag_for(BOARD_PROBE_KEY))
        if resp.status_code == 304:
            return items, True
        if not dry_run and resp.etag:
            self._etag_cache.remember(BOARD_PROBE_KEY, resp.etag)
        board_msgs = (resp.body or {}).get("messages") if isinstance(resp.body, dict) else None
        if isinstance(board_msgs, list):
            items.extend(dict(m) for m in board_msgs if isinstance(m, dict))
        return items, False

    def _process_events(
        self, events: Sequence[dict[str, Any]], *, dry_run: bool
    ) -> tuple[list[str], list[dict[str, Any]], list[str], list[str]]:
        planned, delivered, woken, dropped = [], [], [], []
        for msg in events:
            eid = str(msg.get("id") or "").strip()
            if not eid or self.state.has(eid):
                continue
            hops = int(msg.get("hops") or 0)
            if hops > MAX_HOPS:
                log.warning("rd-barb postman dropped %s: hop limit exceeded (%d > %d)", eid, hops, MAX_HOPS)
                try:
                    self.guard.admit(msg)
                except RelayRejected:
                    pass
                dropped.append(eid)
                continue
            planned.append(eid)
            if dry_run:
                continue
            try:
                self.guard.admit(msg)
            except RelayRejected as exc:
                log.warning("rd-barb postman rejected %s: %s", eid, exc)
                dropped.append(eid)
                continue
            self.state.mark(eid)
            self.state.save(self._state_path)
            delivered.append(msg)
            recipient = str(msg.get("to") or "rd-barb")
            if classify_wake(msg, target_agent="rd-barb" if recipient == "*" else recipient) == "wake":
                woken.append(eid)
        return planned, delivered, woken, dropped

    def _maybe_heartbeat(self, now_ts: float) -> bool:
        if self._last_heartbeat_at is not None and (now_ts - self._last_heartbeat_at) < HEARTBEAT_INTERVAL_S:
            return False
        self._last_heartbeat_at = now_ts
        key = f"rd-barb-hb-{int(now_ts // HEARTBEAT_INTERVAL_S)}"
        hb = {"kind": "update", "urgency": "low", "summary": "rd-barb heartbeat: ok", "body": "", "dedupe_key": key}
        if self._webhook(hb).get("ok"):
            if self._in_outage:
                self._in_outage = self._outage_fallback_posted = False
                self._post_board_status("[rd-barb heartbeat] webhook recovered; normal relay resumed.")
                self._flush_pending_relays()
            return True
        self._enter_outage()
        return False

    def _post_board_status(self, line: str) -> None:
        clean = " ".join(redact_sensitive_content(line).split())
        if "security-confidential" in clean.lower():
            raise ValueError("refusing confidential marker on board fallback")
        self._board_fallback(clean)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="plan one tick and write nothing")
    args = parser.parse_args(argv)
    print(json.dumps({"ok": True, "dry_run": bool(args.dry_run), "mode": "manual_promotion_only"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
