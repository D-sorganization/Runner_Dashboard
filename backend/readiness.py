"""Readiness probe infrastructure for runner-dashboard (issue #332).

Provides composable, protocol-typed probes that are aggregated by
``readyz_check()``.  Each probe performs a single lightweight check and
returns a ``(status, detail)`` pair.

``status`` values:
  - ``"ok"``       — component is healthy
  - ``"degraded"`` — component is present but not fully healthy
  - ``"down"``     — component is unavailable

The aggregate readyz result returns HTTP 200 only when every probe
reports ``"ok"``.  HTTP 503 is returned otherwise with a structured body
showing the per-component status.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Literal, Protocol, runtime_checkable

log = logging.getLogger("dashboard.readiness")

ProbeStatus = Literal["ok", "degraded", "down"]


@runtime_checkable
class Probe(Protocol):
    """Protocol for a single readiness probe."""

    name: str

    async def check(self) -> tuple[ProbeStatus, str | None]:
        """Return (status, detail) where detail may be None when status is ok."""
        ...


# ---------------------------------------------------------------------------
# Concrete probes
# ---------------------------------------------------------------------------


class GhTokenProbe:
    """Check that GH_TOKEN is present in the environment.

    We do not make a live GitHub API call here — that would introduce I/O
    latency and a GitHub-outage → restart-loop regression (#332).  The probe
    only verifies the token is loaded; actual API reachability is surfaced via
    ``/api/health`` (the human-readable composite view).
    """

    name = "github_token"

    async def check(self) -> tuple[ProbeStatus, str | None]:
        token = os.environ.get("GH_TOKEN", "").strip()
        if token:
            return "ok", None
        return "down", "GH_TOKEN env var not set"


class GhCliProbe:
    """Check that the ``gh`` CLI binary is available in PATH."""

    name = "gh_cli"

    async def check(self) -> tuple[ProbeStatus, str | None]:
        if shutil.which("gh") is not None:
            return "ok", None
        return "down", "'gh' not found in PATH"


class LeaseDbProbe:
    """Check that the replay/lease SQLite store can be read."""

    name = "lease_db"

    def __init__(self, db_path: str | None = None) -> None:
        from pathlib import Path

        if db_path is None:
            db_path = str(Path.home() / "actions-runners" / "dashboard" / "replay.db")
        self._db_path = db_path

    async def check(self) -> tuple[ProbeStatus, str | None]:
        from pathlib import Path

        p = Path(self._db_path)
        if not p.parent.exists():
            # Parent directory not yet created — acceptable during cold start.
            return "degraded", f"db directory {p.parent} does not exist yet"
        if not p.exists():
            # DB file will be auto-created by SQLite on first write.
            return "ok", None
        try:
            import sqlite3

            con = sqlite3.connect(str(p), timeout=1)
            con.execute("SELECT 1")
            con.close()
            return "ok", None
        except Exception as exc:  # noqa: BLE001
            return "down", f"sqlite read failed: {exc}"


class PushDbProbe:
    """Check that the push subscriptions SQLite DB is readable."""

    name = "push_db"

    async def check(self) -> tuple[ProbeStatus, str | None]:
        try:
            import push  # noqa: PLC0415

            db_path = push.DEFAULT_DB_PATH
            if not db_path.exists():
                # DB not yet created — OK, subscriptions are optional.
                return "ok", None
            import sqlite3

            con = sqlite3.connect(str(db_path), timeout=1)
            con.execute("SELECT 1")
            con.close()
            return "ok", None
        except Exception as exc:  # noqa: BLE001
            return "down", f"push db read failed: {exc}"


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

_DEFAULT_PROBES: list[Probe] = [
    GhTokenProbe(),
    GhCliProbe(),
    LeaseDbProbe(),
    PushDbProbe(),
]


async def aggregate(probes: list[Probe]) -> tuple[int, dict]:
    """Run all probes concurrently and return (http_status, response_body).

    Returns HTTP 200 when all probes report ``"ok"``, HTTP 503 otherwise.
    """
    results: list[tuple[str, tuple[ProbeStatus, str | None]]] = []
    checks_coros = [(p.name, p.check()) for p in probes]

    async def _run(name: str, coro: object) -> tuple[str, tuple[ProbeStatus, str | None]]:
        try:
            result = await coro  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            log.warning("readyz probe %r raised: %s", name, exc)
            result = ("down", str(exc))
        return name, result

    results = await asyncio.gather(*[_run(n, c) for n, c in checks_coros])

    checks_payload: dict[str, str | dict] = {}
    any_down = False
    any_degraded = False
    for name, (status, detail) in results:
        if status == "down":
            any_down = True
        elif status == "degraded":
            any_degraded = True
        if detail is not None:
            checks_payload[name] = {"status": status, "detail": detail}
        else:
            checks_payload[name] = status

    if any_down:
        overall = "down"
        http_status = 503
    elif any_degraded:
        overall = "degraded"
        http_status = 503
    else:
        overall = "ok"
        http_status = 200

    return http_status, {"status": overall, "checks": checks_payload}


def get_default_probes() -> list[Probe]:
    """Return the default probe list (importable for tests)."""
    return list(_DEFAULT_PROBES)
