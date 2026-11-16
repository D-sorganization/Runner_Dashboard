"""Signed Barb lease attestation for hub→peer staff dispatches (#4357).

The Barb continuity store is hub-local; a peer must not re-check the lease against its
own store when the originating hub already fenced the write. The hub signs a short-lived
claim after a successful origin fence; only a trusted fleet forwarder (peer principal or
``HUB_FLEET_TOKEN`` bearer) may present it, and the signature must match the declared
``X-Barb-*`` headers on the same request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from staff.barb_lease_fence import BarbClaim

log = logging.getLogger("dashboard.staff.barb_origin_fence")

OriginEpochLookup = Callable[[str, str], int | None]
_origin_epoch_lookup: OriginEpochLookup | None = None

ORIGIN_FENCE_HEADER = "x-barb-origin-fence"
DEFAULT_TTL_SECONDS = 300
_DEFAULT_MAX_JTI_CACHE = 4096


def _signing_key() -> str:
    return os.environ.get("HUB_FLEET_TOKEN") or os.environ.get("SESSION_SECRET") or ""


class _OriginFenceReplayGuard:
    """Bounded single-use jti cache and per-scope lease epoch floor for peer verification."""

    def __init__(
        self,
        *,
        ttl_seconds: float = float(DEFAULT_TTL_SECONDS),
        max_jti: int = _DEFAULT_MAX_JTI_CACHE,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_jti = max(1, int(max_jti))
        self._lock = threading.RLock()
        self._seen_jti: OrderedDict[str, float] = OrderedDict()
        self._max_epoch_by_scope: dict[str, int] = {}

    def reset(self) -> None:
        with self._lock:
            self._seen_jti.clear()
            self._max_epoch_by_scope.clear()

    def _prune_jti(self, now: float) -> None:
        cutoff = now - self._ttl_seconds
        for jti in [k for k, ts in self._seen_jti.items() if ts < cutoff]:
            self._seen_jti.pop(jti, None)

    def lease_epoch_floor(
        self,
        scope: str,
        *,
        origin_node: str = "",
        require_origin_epoch: bool = False,
    ) -> int | None:
        """Newest lease epoch this peer must enforce for ``scope``.

        Combines in-memory attestations, the local continuity store, and — when
        ``origin_node`` is set — the authoritative lease epoch on that origin hub.
        Returns ``None`` when ``require_origin_epoch`` is set and the origin epoch
        cannot be determined (fail closed).
        """
        norm = scope.strip()
        if not norm:
            return 0
        with self._lock:
            floor = self._max_epoch_by_scope.get(norm, 0)
        try:
            from staff.barb_continuity import get_barb_continuity_store  # noqa: PLC0415

            row = (
                get_barb_continuity_store()
                ._conn.execute(  # noqa: SLF001
                    "SELECT epoch FROM barb_scoped_leases WHERE scope = ?", (norm,)
                )
                .fetchone()
            )
            if row is not None:
                floor = max(floor, int(row["epoch"]))
        except Exception:  # noqa: BLE001
            pass
        origin = origin_node.strip()
        if origin:
            hub_epoch = _origin_hub_epoch_floor(norm, origin)
            if hub_epoch is None:
                if require_origin_epoch:
                    return None
            else:
                floor = max(floor, int(hub_epoch))
                with self._lock:
                    self._max_epoch_by_scope[norm] = max(self._max_epoch_by_scope.get(norm, 0), int(hub_epoch))
        return floor

    def note_epoch_seen(self, scope: str, epoch: int) -> None:
        norm = scope.strip()
        if not norm or epoch < 1:
            return
        with self._lock:
            self._max_epoch_by_scope[norm] = max(self._max_epoch_by_scope.get(norm, 0), int(epoch))

    def consume_jti(self, jti: str, *, now: float) -> bool:
        """Record ``jti``; return False when it was already consumed within the TTL window."""
        token = jti.strip()
        if not token:
            return False
        with self._lock:
            self._prune_jti(now)
            if token in self._seen_jti:
                return False
            if len(self._seen_jti) >= self._max_jti:
                log.warning(
                    "barb_origin_fence: jti replay cache at capacity (%s); rejecting new attestation",
                    self._max_jti,
                )
                return False
            self._seen_jti[token] = now
            return True


_REPLAY_GUARD = _OriginFenceReplayGuard()


def reset_origin_fence_replay_guard() -> None:
    """Test hook: clear jti cache and in-memory epoch floors."""
    _REPLAY_GUARD.reset()


def set_origin_epoch_lookup(lookup: OriginEpochLookup | None) -> None:
    """Test/prod hook: resolve authoritative scope epoch on ``origin_node``."""
    global _origin_epoch_lookup
    _origin_epoch_lookup = lookup


def configure_origin_fence_replay_guard_for_test(*, max_jti: int | None = None) -> None:
    """Test hook: tune replay-guard bounds."""
    if max_jti is not None:
        _REPLAY_GUARD._max_jti = max(1, int(max_jti))  # noqa: SLF001


def _default_origin_epoch_lookup(scope: str, origin_node: str) -> int | None:
    """GET origin hub ``/barb/leases`` and return the live epoch for ``scope``."""
    try:
        import httpx
        from dashboard_config import HOSTNAME  # noqa: PLC0415

        from staff import fleet as staff_fleet  # noqa: PLC0415

        want = origin_node.strip()
        if not want:
            return None
        nodes = staff_fleet.peer_nodes()
        base = nodes.get(want)
        if not base:
            local = (HOSTNAME or "").strip()
            if want == local or want.lower() == local.lower():
                base = os.environ.get("DASHBOARD_LOOPBACK_ORIGIN", "http://127.0.0.1:8321").rstrip("/")
            else:
                return None
        url = f"{base}/api/v1/staff/barb/leases"
        with httpx.Client(timeout=staff_fleet.PEER_TIMEOUT_SECONDS) as client:
            resp = client.get(url, headers=staff_fleet.fleet_headers())
            if resp.status_code != 200:
                return None
            data = resp.json()
        if not isinstance(data, dict):
            return None
        for row in data.get("leases") or []:
            if isinstance(row, dict) and str(row.get("scope") or "") == scope:
                return int(row.get("epoch") or 0)
        return 0
    except Exception as exc:  # noqa: BLE001
        log.debug("origin hub lease epoch lookup failed for %s@%s: %s", scope, origin_node, exc)
        return None


def _origin_hub_epoch_floor(scope: str, origin_node: str) -> int | None:
    lookup = _origin_epoch_lookup or _default_origin_epoch_lookup
    return lookup(scope, origin_node)


def sign_barb_origin_fence(claim: BarbClaim, *, origin_node: str) -> str:
    """Build ``X-Barb-Origin-Fence`` after the origin hub validated the lease (#4357)."""
    key = _signing_key()
    payload = {
        "scope": claim.scope,
        "epoch": claim.epoch,
        "holder": claim.holder,
        "origin_node": origin_node,
        "iat": int(time.time()),
        "jti": secrets.token_hex(16),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii")
    sig = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_barb_origin_fence(
    header: str | None,
    claim: BarbClaim,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    expected_origin_node: str = "",
) -> bool:
    """True when ``header`` is a valid, unexpired, non-replayed attestation for ``claim``."""
    key = _signing_key()
    if not key or not header or "." not in header:
        return False
    try:
        payload_b64, sig = header.split(".", 1)
        expected = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        payload: dict[str, Any] = json.loads(payload_json)
        if not isinstance(payload, dict):
            return False
        iat = payload.get("iat")
        if not isinstance(iat, (int, float)):
            return False
        now = time.time()
        if now - iat > ttl_seconds or iat - now > 60:
            return False
        att_scope = str(payload.get("scope") or "")
        if att_scope != claim.scope:
            return False
        att_epoch = int(payload.get("epoch") or 0)
        if att_epoch != claim.epoch or att_epoch < 1:
            return False
        declared_holder = str(payload.get("holder") or "")
        if declared_holder != (claim.holder or ""):
            return False
        origin_in_att = str(payload.get("origin_node") or "").strip()
        if not origin_in_att:
            return False
        expected = expected_origin_node.strip()
        if expected and origin_in_att != expected:
            return False
        require_origin_epoch = bool(expected) and origin_in_att == expected
        floor = _REPLAY_GUARD.lease_epoch_floor(
            att_scope,
            origin_node=origin_in_att,
            require_origin_epoch=require_origin_epoch,
        )
        if floor is None or att_epoch < floor:
            return False
        jti = str(payload.get("jti") or "").strip()
        if not jti or not _REPLAY_GUARD.consume_jti(jti, now=now):
            return False
        _REPLAY_GUARD.note_epoch_seen(att_scope, att_epoch)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("verify_barb_origin_fence failed: %s", exc)
        return False


def apply_origin_fence_trust(
    claim: BarbClaim | None,
    header: str | None,
    *,
    trusted_forwarder: bool,
    expected_origin_node: str = "",
) -> BarbClaim | None:
    """Return a claim marked origin-validated when the attestation checks out (#4357)."""
    if claim is None or not trusted_forwarder:
        return claim
    if verify_barb_origin_fence(header, claim, expected_origin_node=expected_origin_node):
        return BarbClaim(
            scope=claim.scope,
            epoch=claim.epoch,
            caller_id=claim.caller_id,
            holder=claim.holder,
            lease_validated_at_origin=True,
        )
    return claim
