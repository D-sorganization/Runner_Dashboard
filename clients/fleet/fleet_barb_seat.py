"""Barb seat client methods: scoped lease checkout, heartbeat, turnover and release.

Mixed into :class:`fleet_client.FleetClient`. The routes live in ``backend/routers/staff_barb.py``
under ``/api/v1/staff/barb``. ``holder`` is optional everywhere: it is omitted from the request when
not given so a hub that binds the holder to the authenticated principal can fill it in. Every call
goes through ``FleetClient.request``, which sends ``X-Requested-With: XMLHttpRequest`` and the
configured bearer token.
"""

from __future__ import annotations

import re
from typing import Any

from fleet_validators import FleetAPIError, _check, _opt_text, _positive_int, _text

BARB_PREFIX = "/api/v1/staff/barb"
_SCOPE = re.compile(r"^barb:[a-z0-9][a-z0-9._-]{0,57}$")
MIN_TTL_SEC = 60
MAX_TTL_SEC = 14400  # the store clamps to 4h; reject larger values instead of silently shortening


def _scope(value: Any) -> str:
    text = _text(value, "scope", 64, form="line")
    _check(bool(_SCOPE.match(text)), "scope must look like 'barb:<subrole>', e.g. barb:front-door")
    return text


def _lease_of(body: Any) -> dict[str, Any]:
    detail = body.get("detail") if isinstance(body, dict) else None
    source = detail if isinstance(detail, dict) else body
    lease = source.get("lease") if isinstance(source, dict) else None
    return lease if isinstance(lease, dict) else {}


class BarbSeatMixin:
    """Take, hold and hand over a scoped Barb seat (one holder per scope)."""

    def request(self, method: str, path: str, **kwargs: Any) -> Any:  # pragma: no cover - provided by FleetClient
        raise NotImplementedError

    def barb_seat_status(self, turnover_limit: int = 5) -> Any:
        """Continuity snapshot: authority, scoped leases, recent turnover log and task inventory."""
        limit = _positive_int(turnover_limit, "turnover_limit")
        _check(limit <= 100, "turnover_limit must be <= 100")
        return self.request("GET", f"{BARB_PREFIX}/continuity", params={"turnover_limit": limit})

    def barb_seat_checkout(self, scope: str, ttl_sec: int | None = None, holder: str | None = None) -> Any:
        """Check out a scope. A 409 is re-raised as 'seat held by X until T': do not act on it."""
        body: dict[str, Any] = {"scope": _scope(scope)}
        if ttl_sec is not None:
            ttl = _positive_int(ttl_sec, "ttl_sec")
            _check(MIN_TTL_SEC <= ttl <= MAX_TTL_SEC, f"ttl_sec must be between {MIN_TTL_SEC} and {MAX_TTL_SEC}")
            body["ttl_sec"] = ttl
        if holder is not None:
            body["holder"] = _text(holder, "holder", 128, form="line")
        try:
            return self.request("POST", f"{BARB_PREFIX}/leases/checkout", body=body)
        except FleetAPIError as exc:
            if exc.status != 409:
                raise
            lease = _lease_of(exc.body)
            who = lease.get("holder") or "another agent"
            until = lease.get("expires_at") or "an unknown time"
            message = (
                f"Barb seat {body['scope']} is held by {who} until {until}. "
                "Do not act as this seat; pick another scope or wait for expiry."
            )
            raise FleetAPIError(409, {"detail": message, "conflict": True, "lease": lease}) from None

    def barb_seat_heartbeat(self, scope: str, epoch: int) -> Any:
        """Extend the lease. Needs a hub with POST /barb/leases/heartbeat; a 404 is reported as such."""
        body = {"scope": _scope(scope), "epoch": _positive_int(epoch, "epoch")}
        try:
            return self.request("POST", f"{BARB_PREFIX}/leases/heartbeat", body=body)
        except FleetAPIError as exc:
            if exc.status != 404:
                raise
            message = (
                "Barb heartbeat returned 404: the hub is older than this client (no "
                f"{BARB_PREFIX}/leases/heartbeat route) or has no lease for {body['scope']}. "
                "Re-run barb seat status; do not assume the seat is still yours."
            )
            raise FleetAPIError(404, {"detail": message, "original": exc.body}) from None

    def barb_seat_turnover(
        self,
        scope: str,
        summary: str,
        next_actions: list[str] | None = None,
        epoch: int | None = None,
        holder: str | None = None,
    ) -> Any:
        """Append the shift turnover entry that the release compliance check requires."""
        actions = [] if next_actions is None else next_actions
        _check(isinstance(actions, list) and len(actions) <= 20, "next_actions must be an array of at most 20 items")
        body: dict[str, Any] = {
            "scope": _scope(scope),
            "summary": _text(summary, "summary", 2000),
            "next_actions": [_text(item, "next_action", 300) for item in actions],
        }
        if epoch is not None:
            body["epoch"] = _positive_int(epoch, "epoch")
        if holder is not None:
            body["holder"] = _text(holder, "holder", 128, form="line")
        return self.request("POST", f"{BARB_PREFIX}/turnover", body=body)

    def barb_seat_release(
        self, scope: str, epoch: int, turnover_id: str | None = None, holder: str | None = None
    ) -> Any:
        """Release the seat; the hub refuses (409) unless a turnover was written."""
        body: dict[str, Any] = {"scope": _scope(scope), "epoch": _positive_int(epoch, "epoch")}
        tid = _opt_text(turnover_id, "turnover_id", 64)
        if tid is not None:
            body["turnover_id"] = tid
        if holder is not None:
            body["holder"] = _text(holder, "holder", 128, form="line")
        return self.request("POST", f"{BARB_PREFIX}/leases/release", body=body)
