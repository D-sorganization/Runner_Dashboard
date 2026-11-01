"""Fire-and-forget inbox webhook fan-out for off-tailnet agent-bus consumers (#3682 slice 4).

When a message lands in a principal's inbox address (role or registered session),
POST a JSON envelope to each configured ``inbox_webhook_url``. Delivery is best-effort:
one attempt, bounded timeout, no retries, and failures never block the send path.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dashboard_config.timeouts import HttpTimeout

log = logging.getLogger("dashboard.agent_bus.inbox_webhook")

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-bus-wh")
_PENDING: set[Future[None]] = set()
_GUARD = threading.Lock()


def _safe_url_label(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.hostname or parsed.netloc}{path}"


def _valid_webhook_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def default_inbox_roles(principal_id: str) -> list[str]:
    if principal_id == "rd-barb":
        return ["rd-barb"]
    if principal_id.startswith("agent-"):
        return [principal_id.removeprefix("agent-")]
    return [principal_id]


def _roles_for_principal(principal: Any) -> list[str]:
    explicit = getattr(principal, "inbox_roles", None) or []
    if explicit:
        return [str(r) for r in explicit if str(r).strip()]
    return default_inbox_roles(str(principal.id))


def webhook_urls_for_address(to_address: str, *, store: Any) -> list[str]:
    """Resolve webhook URLs for an inbox ``to_address`` (role, session id, or work-item address)."""
    if "#" in to_address:
        return []
    roles: set[str]
    row = store.get_principal(to_address)
    if row is not None:
        roles = {row.role}
    else:
        roles = {to_address}

    from identity import identity_manager  # noqa: PLC0415

    urls: list[str] = []
    seen: set[str] = set()
    for prin in identity_manager.principals.values():
        url = getattr(prin, "inbox_webhook_url", None)
        if not url or not _valid_webhook_url(str(url)):
            continue
        url_s = str(url).strip()
        if not any(role in roles for role in _roles_for_principal(prin)):
            continue
        if url_s in seen:
            continue
        seen.add(url_s)
        urls.append(url_s)
    return urls


def _post_once(url: str, payload: bytes) -> None:
    label = _safe_url_label(url)
    try:
        req = Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Runner-Dashboard-AgentBus/1"},
            method="POST",
        )
        with urlopen(req, timeout=HttpTimeout.AGENT_BUS_INBOX_WEBHOOK_S) as resp:  # noqa: S310
            resp.read(256)
    except Exception as exc:  # noqa: BLE001 - best-effort fan-out must never raise
        log.info("agent_bus inbox webhook failed for %s: %s", label, type(exc).__name__)


def schedule_inbox_webhooks(to_address: str, message: dict[str, Any], *, store: Any) -> None:
    urls = webhook_urls_for_address(to_address, store=store)
    if not urls:
        return
    body = json.dumps({"event": "inbox_message", "message": message}, separators=(",", ":")).encode()
    for url in urls:
        fut = _POOL.submit(_post_once, url, body)
        with _GUARD:
            _PENDING.add(fut)

        def _done(f: Future[None], *, _url: str = url) -> None:
            with _GUARD:
                _PENDING.discard(f)
            try:
                f.result()
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "agent_bus inbox webhook task failed for %s: %s",
                    _safe_url_label(_url),
                    type(exc).__name__,
                )

        fut.add_done_callback(_done)


def drain_for_tests(timeout: float = 5.0) -> int:
    """Wait for outstanding webhook tasks (tests only). Returns remaining pending count."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _GUARD:
            pending = [f for f in _PENDING if not f.done()]
        if not pending:
            return 0
        time.sleep(0.05)
    with _GUARD:
        return len([f for f in _PENDING if not f.done()])
