"""Off-tailnet Barb scoped lease requests via rd-threads (#3301)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from memory.redact import contains_secret
from staff.barb_continuity import DEFAULT_LEASE_TTL_SECONDS, BarbContinuityStore, normalize_scope

from scripts import rd_threads_envelope as envelope

ACTIONS = frozenset({"status", "checkout", "heartbeat", "release"})


def remote_holders_from_env() -> frozenset[str]:
    raw = os.environ.get("BARB_REMOTE_HOLDERS", "").strip()
    return frozenset(p.strip() for p in raw.split(",") if p.strip()) if raw else frozenset()


def build_request(
    action: str,
    scope: str,
    *,
    sender: str,
    holder: str | None = None,
    epoch: int | None = None,
    persona: str = "",
    machine: str = "",
    summary: str = "",
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    turnover: dict[str, Any] | None = None,
    force: bool = False,
    now: float,
    to: str = "rd-barb",
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise ValueError(f"unknown barb_lease action {action!r}")
    seat = (holder or sender).strip()
    block: dict[str, Any] = {"action": action, "scope": normalize_scope(scope), "holder": seat}
    if epoch is not None:
        block["epoch"] = int(epoch)
    if action == "checkout":
        block.update(persona=persona, machine=machine, summary=summary, ttl_seconds=int(ttl_seconds), force=bool(force))
    elif action == "heartbeat":
        if epoch is None:
            raise ValueError("heartbeat requires epoch")
        block["ttl_seconds"] = int(ttl_seconds)
    elif action == "release":
        if epoch is None:
            raise ValueError("release requires epoch")
        if turnover is not None:
            block["turnover"] = turnover
    msg = {
        "kind": "update",
        "from": sender.strip(),
        "to": to,
        "summary": summary.strip() or f"barb lease {action} {block['scope']}",
        "body": json.dumps(block, sort_keys=True, separators=(",", ":")),
        "barb_lease": block,
    }
    return envelope.seal(msg, now)


def _answer(request: dict[str, Any], block: dict[str, Any], *, now: float) -> dict[str, Any]:
    msg = {
        "kind": "answer",
        "from": "rd-barb",
        "to": str(request.get("from") or ""),
        "summary": "barb lease exchange result",
        "body": json.dumps(block, sort_keys=True, separators=(",", ":")),
        "barb_lease": block,
    }
    return envelope.seal(msg, now)


def _pack(
    store: BarbContinuityStore,
    now: datetime,
    request: dict[str, Any],
    *,
    ok: bool,
    error: str = "",
    holder: str = "",
    epoch: int = 0,
    extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body: dict[str, Any] = {
        "ok": ok,
        "error": error,
        "holder": holder,
        "epoch": epoch,
        "leases": store.list_leases(now=now),
        **(extra or {}),
    }
    if ok:
        body.pop("error", None)
    return body, _answer(request, body, now=now.timestamp())


def _lease_row(store: BarbContinuityStore, scope: str, now: datetime) -> dict[str, Any] | None:
    return next((r for r in store.list_leases(include_expired=True, now=now) if r["scope"] == scope), None)


def apply_request(
    store: BarbContinuityStore,
    msg: dict[str, Any],
    allowlist: frozenset[str],
    now: datetime | None = None,
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dt = now if now is not None else datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    if problems := envelope.validate(msg):
        return _pack(store, dt, msg, ok=False, error="invalid_envelope", extra={"problems": problems})

    block = msg.get("barb_lease")
    if not isinstance(block, dict):
        return _pack(store, dt, msg, ok=False, error="missing_barb_lease")

    sender = str(msg.get("from") or "").strip()
    holder = str(block.get("holder") or "").strip()
    action = str(block.get("action") or "")
    if sender not in allowlist:
        return _pack(store, dt, msg, ok=False, error="sender_not_allowed")
    if holder != sender:
        return _pack(store, dt, msg, ok=False, error="holder_sender_mismatch")
    if contains_secret(msg):
        return _pack(store, dt, msg, ok=False, error="secret_forbidden")
    if action not in ACTIONS:
        return _pack(store, dt, msg, ok=False, error="unknown_action")
    if block.get("force"):
        return _pack(store, dt, msg, ok=False, error="force_forbidden")
    try:
        scope = normalize_scope(str(block.get("scope") or ""))
    except ValueError as exc:
        return _pack(store, dt, msg, ok=False, error="invalid_scope", extra={"detail": str(exc)})

    if action == "status":
        return _pack(store, dt, msg, ok=True)
    if dry_run:
        return _pack(store, dt, msg, ok=True, extra={"dry_run": True, "action": action})

    if action == "checkout":
        res = store.checkout_lease(
            scope=scope,
            holder=holder,
            persona=str(block.get("persona") or ""),
            machine=str(block.get("machine") or ""),
            summary=str(block.get("summary") or ""),
            ttl_seconds=int(block.get("ttl_seconds") or DEFAULT_LEASE_TTL_SECONDS),
            force=False,
            now=dt,
        )
        lease = res["lease"]
        if not res["acquired"]:
            return _pack(
                store,
                dt,
                msg,
                ok=False,
                error="lease_conflict",
                holder=str(lease.get("holder") or ""),
                epoch=int(lease.get("epoch") or 0),
            )
        return _pack(store, dt, msg, ok=True, holder=holder, epoch=int(lease["epoch"]))

    want = int(block.get("epoch") or 0)
    if action == "heartbeat":
        res = store.heartbeat_lease(
            scope=scope,
            holder=holder,
            epoch=want,
            ttl_seconds=int(block.get("ttl_seconds") or DEFAULT_LEASE_TTL_SECONDS),
            now=dt,
        )
        if not res["renewed"]:
            reason = str(res.get("reason") or "heartbeat_failed")
            lease = res.get("lease") or {}
            return _pack(
                store,
                dt,
                msg,
                ok=False,
                error="stale_epoch" if reason == "stale_epoch" else reason,
                holder=str(lease.get("holder") or holder),
                epoch=int(lease.get("epoch") or want),
            )
        lease = res["lease"]
        return _pack(store, dt, msg, ok=True, holder=holder, epoch=int(lease["epoch"]))

    cur = _lease_row(store, scope, dt)
    if cur is None or not cur.get("active"):
        return _pack(store, dt, msg, ok=False, error="not_found")
    if int(cur["epoch"]) != want:
        return _pack(
            store,
            dt,
            msg,
            ok=False,
            error="stale_epoch",
            holder=str(cur.get("holder") or ""),
            epoch=int(cur["epoch"]),
        )
    turnover_raw = block.get("turnover")
    if not isinstance(turnover_raw, dict) or not str(turnover_raw.get("summary") or "").strip():
        return _pack(store, dt, msg, ok=False, error="turnover_required", holder=holder, epoch=want)

    res = store.release_lease(
        scope=scope,
        holder=holder,
        require_turnover=True,
        turnover={
            "summary": str(turnover_raw.get("summary") or ""),
            "persona": str(turnover_raw.get("persona") or block.get("persona") or ""),
            "machine": str(turnover_raw.get("machine") or block.get("machine") or ""),
            "completed": turnover_raw.get("completed") or [],
            "in_progress": turnover_raw.get("in_progress") or [],
            "next_actions": turnover_raw.get("next_actions") or [],
            "blockers": turnover_raw.get("blockers") or [],
        },
        now=dt,
    )
    if not res["released"]:
        reason = str(res.get("reason") or "release_failed")
        err = "turnover_required" if reason == "turnover_required_before_release" else reason
        return _pack(store, dt, msg, ok=False, error=err, holder=holder, epoch=want)
    return _pack(store, dt, msg, ok=True, extra={"released": True, "scope": scope})
