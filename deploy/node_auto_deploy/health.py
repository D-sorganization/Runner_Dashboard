"""Post-deploy health probes (/api/health and optional staff summary)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def wait_for_health(
    port: int,
    *,
    delays: tuple[float, ...] = (1, 2, 4, 8, 15, 15, 15, 15),
    probe: Callable[[str], tuple[bool, str]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Probe localhost health before and after bounded retry delays."""
    health_probe = probe or (lambda url: probe_health(url, timeout=2.0))
    url = f"http://127.0.0.1:{port}"
    if health_probe(url)[0]:
        return True
    for delay in delays:
        sleep(delay)
        if health_probe(url)[0]:
            return True
    return False


def probe_health(base_url: str, *, timeout: float = 15.0) -> tuple[bool, str]:
    url = f"{base_url.rstrip('/')}/api/health"
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return False, str(exc)
    if not isinstance(payload, dict):
        return False, "invalid health json"
    status = str(payload.get("status") or "")
    gh = str(payload.get("github_api") or "")
    if status == "healthy" and gh == "connected":
        return True, "healthy"
    return False, f"status={status} github_api={gh}"


def probe_staff_summary(base_url: str, token: str | None, *, timeout: float = 15.0) -> tuple[bool, str]:
    if not token:
        return True, "staff summary skipped (no probe token)"
    url = f"{base_url.rstrip('/')}/api/staff/summary"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return False, f"staff summary failed: {exc}"
    if not isinstance(payload, dict):
        return False, "invalid staff summary json"
    return True, "staff summary ok"


def staff_in_flight_count(summary: dict[str, Any]) -> int:
    rows = summary.get("in_flight")
    if not isinstance(rows, list):
        return 0
    return len(rows)
