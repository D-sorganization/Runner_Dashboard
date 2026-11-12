"""Barb authority configuration and the epoch-fenced delivery client (#1800, BR-06).

Defaults recorded for the owner (RM#1853 ruling 2026-10-11; pending owner confirmation):

* the authority is the machine-registry ``hub`` (node-a), configurable via
  ``BARB_AUTHORITY_NODE`` / ``BARB_AUTHORITY_URL`` / ``BARB_AUTHORITY_EPOCH`` or a
  ``barb_authority`` key in the fleet manifest (:func:`registry_hub`);
* RPO = 0 means "an acknowledged write is durably committed at the authority"
  (``synchronous=FULL``); surviving loss of the authority's disk needs a synchronous standby,
  a recorded follow-up that is NOT implemented here;
* failover is manual: the operator bumps the epoch, which fences the old authority.

With nothing configured the role is ``UNSET`` and the dashboard keeps its legacy single-node
behaviour, so this module changes nothing until an operator opts in.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger("dashboard.staff.barb_authority")

#: ``(url, json_body, headers) -> (status_code, json_body)``. Must raise only ``OSError`` family
#: errors (``ConnectionError``, ``TimeoutError``) when the peer cannot be reached.
Transport = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, dict[str, Any]]]

_ROUTES = {"run_card": "/api/v1/staff/threads/{thread_id}/relay-card"}


class AuthorityRole(str, Enum):
    UNSET = "unset"
    AUTHORITY = "authority"
    REPLICA = "replica"


class DeliveryOutcome(str, Enum):
    DELIVERED = "delivered"  # applied, or an idempotent duplicate
    STALE = "stale"  # the authority already holds an equal-or-newer version: nothing to deliver


class AuthorityUnavailable(RuntimeError):
    """The authority could not be reached or is overloaded (retry with backoff)."""


class AuthorityEpochMismatch(RuntimeError):
    """The authority fenced this sender's epoch (re-resolve configuration, then retry)."""


class AuthorityRejected(RuntimeError):
    """The authority permanently rejected the delivery (operator attention; never retried)."""


@dataclass(frozen=True)
class AuthorityConfig:
    role: AuthorityRole
    node: str = ""
    url: str = ""
    epoch: int = 0
    local_node: str = ""

    @property
    def is_authority(self) -> bool:
        return self.role is AuthorityRole.AUTHORITY

    @property
    def is_replica(self) -> bool:
        return self.role is AuthorityRole.REPLICA

    @classmethod
    def from_env(cls, environ: Mapping[str, str], *, local_node: str) -> AuthorityConfig:
        """Pre: a configured epoch is an integer >= 1 and the URL is http(s).

        Post: ``UNSET`` when nothing is configured; otherwise AUTHORITY (this node is the named
        authority) or REPLICA (it needs the authority's URL). Raises ``ValueError`` on bad input.
        """
        node = (environ.get("BARB_AUTHORITY_NODE") or "").strip()
        url = (environ.get("BARB_AUTHORITY_URL") or "").strip().rstrip("/")
        raw_epoch = (environ.get("BARB_AUTHORITY_EPOCH") or "").strip()
        if not node and not url and not raw_epoch:
            return cls(AuthorityRole.UNSET, local_node=local_node)
        if not node:
            raise ValueError("BARB_AUTHORITY_URL/EPOCH set without BARB_AUTHORITY_NODE")
        try:
            epoch = int(raw_epoch) if raw_epoch else 1
        except ValueError as exc:
            raise ValueError(f"BARB_AUTHORITY_EPOCH must be an integer, got {raw_epoch!r}") from exc
        if epoch < 1:
            raise ValueError("BARB_AUTHORITY_EPOCH must be >= 1")
        if url and urllib.parse.urlparse(url).scheme not in ("http", "https"):
            raise ValueError(f"BARB_AUTHORITY_URL must be http(s), got {url!r}")
        if node.lower() == local_node.lower():
            return cls(AuthorityRole.AUTHORITY, node=node, url=url, epoch=epoch, local_node=local_node)
        if not url:
            raise ValueError("a replica needs BARB_AUTHORITY_URL")
        return cls(AuthorityRole.REPLICA, node=node, url=url, epoch=epoch, local_node=local_node)


def registry_hub(registry: Mapping[str, Any]) -> tuple[str, str] | None:
    """Authority named by the fleet manifest: the ``barb_authority`` key, else the ``hub`` machine.

    Returns ``(name, dashboard_url)`` or ``None`` when the manifest names no usable machine.
    """
    machines = [m for m in (registry.get("machines") or []) if isinstance(m, Mapping)]
    wanted = str(registry.get("barb_authority") or "").strip().lower()
    for m in machines:
        name = str(m.get("name") or "")
        if (wanted and name.lower() == wanted) or (not wanted and str(m.get("role") or "") == "hub"):
            url = str(m.get("dashboard_url") or "").rstrip("/")
            if name and url:
                return name, url
    return None


class AuthorityClient:
    """Sends one idempotent, version-stamped, epoch-fenced event to the authority.

    The only sanctioned write transport from a non-authority node (#1809/#1810 add new ``kind`` s).
    """

    def __init__(self, config: AuthorityConfig, transport: Transport, headers: Mapping[str, str] | None = None) -> None:
        if config.role is AuthorityRole.UNSET or not config.url:
            raise ValueError("AuthorityClient needs a configured authority URL")
        self._config = config
        self._transport = transport
        self._headers = dict(headers or {})

    @property
    def epoch(self) -> int:
        return self._config.epoch

    def post(self, kind: str, thread_id: str, run_id: str, version: int, payload: dict[str, Any]) -> DeliveryOutcome:
        """Pre: known ``kind``, non-empty ids, ``version >= 1``.

        Post: DELIVERED for applied/duplicate, STALE when the authority is already ahead; raises
        :class:`AuthorityUnavailable` (retry), :class:`AuthorityEpochMismatch` (re-resolve) or
        :class:`AuthorityRejected` (dead-letter).
        """
        route = _ROUTES.get(kind)
        if route is None:
            raise ValueError(f"unknown delivery kind {kind!r}")
        if version < 1:
            raise ValueError("version must be >= 1")
        if not thread_id or not run_id:
            raise ValueError("thread_id and run_id are required")
        url = self._config.url + route.format(thread_id=urllib.parse.quote(thread_id, safe=""))
        body = {**payload, "run_id": run_id, "version": version, "epoch": self._config.epoch}
        headers = {**self._headers, "Idempotency-Key": f"{kind}:{thread_id}:{run_id}:{version}"}
        try:
            status, data = self._transport(url, body, headers)
        except OSError as exc:
            raise AuthorityUnavailable(f"{self._config.node} unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise AuthorityRejected(f"{self._config.node} returned a non-object body (HTTP {status})")
        code = str(data.get("code") or "")
        if 200 <= status < 300:
            return DeliveryOutcome.DELIVERED
        if status == 409 and code == "stale_version":
            return DeliveryOutcome.STALE
        if status == 409 and code == "stale_authority_epoch":
            raise AuthorityEpochMismatch(f"epoch {self._config.epoch} fenced by {self._config.node}")
        if status == 429 or status >= 500:
            raise AuthorityUnavailable(f"{self._config.node} returned HTTP {status}")
        raise AuthorityRejected(f"{self._config.node} rejected delivery: HTTP {status} {code}")
