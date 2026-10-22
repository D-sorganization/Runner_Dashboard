"""Wait until no staff runs are mid-flight before restarting the dashboard."""

from __future__ import annotations

import json
import time
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


def fetch_staff_summary(base_url: str, token: str | None, *, timeout: float = 15.0) -> dict:
    url = f"{base_url.rstrip('/')}/api/staff/summary"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def wait_for_staff_idle(
    *,
    in_flight_count: Callable[[], int],
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: float = 900.0,
    poll_seconds: float = 30.0,
) -> tuple[bool, str]:
    """Return (ready, detail). Fails closed when timeout elapses with active runs."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        count = in_flight_count()
        if count == 0:
            return True, "no staff runs in flight"
        if time.monotonic() >= deadline:
            return False, f"timed out with {count} staff run(s) in flight"
        sleep(poll_seconds)
