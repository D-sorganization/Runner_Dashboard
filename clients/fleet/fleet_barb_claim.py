"""Client side of the Barb lease-epoch fence (#2734).

The hub fences Barb-authored writes on the scope lease epoch (``backend/staff/barb_lease_fence.py``).
This module remembers the seat the agent last checked out or heartbeated, adds
``X-Barb-Scope`` / ``X-Barb-Lease-Epoch`` (and ``X-Barb-Holder``) to the write calls that act as Barb, and
turns the hub's ``stale_barb_lease`` 409 into an unmistakable "you lost the seat" error. A 409 is never
retried: the write was refused, and replaying it under the same stale epoch could only fail again.

The seat is kept in memory and mirrored to a small JSON file (``FLEET_BARB_SEAT_FILE``, default
``~/.fleet/barb_seat.json``) so one-shot ``fleetctl`` invocations share the seat that a previous
``checkout`` took. Every file operation is best effort; without the file the client simply sends no claim.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fleet_validators import FleetAPIError

STALE_ERROR = "stale_barb_lease"
# Writes made on behalf of a seat. Lease management itself (checkout/heartbeat/release) is deliberately absent.
_CLAIMING = (
    ("POST", re.compile(r"^/api/(?:v1/)?staff/[^/]+/run$")),
    ("POST", re.compile(r"^/api/v1/memory(?:/session-digest)?$")),
    ("PUT", re.compile(r"^/api/priorities/directives$")),
    ("POST", re.compile(r"^/api/v1/staff/barb/(?:turnover|inventory|handoff)$")),
)
_LEASE_PATH = re.compile(r"^/api/v1/staff/barb/leases/(checkout|heartbeat|release)$")


def _seat_file() -> Path:
    override = os.environ.get("FLEET_BARB_SEAT_FILE")
    return Path(override) if override else Path.home() / ".fleet" / "barb_seat.json"


class BarbSeatCache:
    """Seats this agent holds on one hub: ``scope -> {epoch, holder}`` plus the most recent scope."""

    def __init__(self, base_url: str) -> None:
        self._key = base_url
        self._seats: dict[str, dict[str, Any]] = {}
        self._last = ""
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(_seat_file().read_text(encoding="utf-8")).get(self._key) or {}
            self._seats = {s: v for s, v in (data.get("seats") or {}).items() if isinstance(v, dict)}
            self._last = str(data.get("last") or "")
        except (OSError, ValueError, AttributeError):
            self._seats, self._last = {}, ""

    def _save(self) -> None:
        path = _seat_file()
        try:
            try:
                everything = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                everything = {}
            everything[self._key] = {"seats": self._seats, "last": self._last}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(everything), encoding="utf-8")
        except OSError:
            pass  # best effort: no file means no cross-process seat, never a failed call

    def remember(self, lease: dict[str, Any]) -> None:
        scope, epoch = str(lease.get("scope") or ""), lease.get("epoch")
        if scope and isinstance(epoch, int) and epoch >= 1:
            self._seats[scope] = {"epoch": epoch, "holder": str(lease.get("holder") or "")}
            self._last = scope
            self._save()

    def forget(self, scope: str) -> None:
        if self._seats.pop(scope, None) is not None:
            self._last = self._last if self._last in self._seats else next(iter(self._seats), "")
            self._save()

    def seat(self, scope: str | None = None) -> tuple[str, dict[str, Any]] | None:
        name = scope or self._last
        seat = self._seats.get(name)
        return (name, seat) if seat else None


def claim_wanted(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in _CLAIMING)


def stale_error(exc: FleetAPIError, seat_epoch: int | None = None) -> FleetAPIError | None:
    """Translate the hub's ``stale_barb_lease`` 409 into the lost-seat error; ``None`` for any other error."""
    body = exc.body if isinstance(exc.body, dict) else {}
    detail = body.get("detail")
    if exc.status != 409 or not isinstance(detail, dict) or detail.get("error") != STALE_ERROR:
        return None
    claimed = detail.get("claimed_epoch", seat_epoch)
    current, holder, reason = detail.get("current_epoch"), detail.get("holder") or "nobody", detail.get("reason")
    message = (
        f"You lost the Barb seat {detail.get('scope')} (epoch {claimed} -> {current}; now held by {holder}; "
        f"reason: {reason}). The write was NOT applied and will not be retried. "
        "Re-checkout the seat or hand off to the new holder."
    )
    return FleetAPIError(409, {"detail": message, "error": STALE_ERROR, "stale_barb_lease": detail})


class BarbClaimMixin:
    """Mixed into :class:`fleet_client.FleetClient`: automatic claim headers and lost-seat translation."""

    base_url: str
    _seat_cache: BarbSeatCache | None = None

    def _barb_seats(self) -> BarbSeatCache:
        if self._seat_cache is None:
            self._seat_cache = BarbSeatCache(self.base_url)
        return self._seat_cache

    def _barb_claim_headers(self, method: str, path: str, body: Any) -> dict[str, str]:
        if not claim_wanted(method, path):
            return {}
        # Turnover names its scope in the body; every other write acts under the most recent seat.
        scope = body.get("scope") if isinstance(body, dict) and path.endswith(("/turnover", "/inventory")) else None
        found = self._barb_seats().seat(scope)
        if found is None:
            return {}
        name, seat = found
        headers = {"X-Barb-Scope": name, "X-Barb-Lease-Epoch": str(seat["epoch"])}
        if seat.get("holder"):
            headers["X-Barb-Holder"] = str(seat["holder"])
        return headers

    def _barb_observe(self, method: str, path: str, body: Any, result: Any) -> None:
        match = _LEASE_PATH.match(path) if method == "POST" else None
        if match is None or not isinstance(result, dict):
            return
        if match.group(1) == "release":
            if isinstance(body, dict) and body.get("scope"):
                self._barb_seats().forget(str(body["scope"]))
            return
        lease = result.get("lease")
        if isinstance(lease, dict):
            self._barb_seats().remember(lease)

    def _barb_translate(self, exc: FleetAPIError, headers: dict[str, str]) -> FleetAPIError | None:
        epoch = headers.get("X-Barb-Lease-Epoch")
        return stale_error(exc, int(epoch) if epoch and epoch.isdigit() else None)
