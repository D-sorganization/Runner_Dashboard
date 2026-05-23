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
import math
import os
import shutil
import time
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
# Runner health probe (issue #712)
# ---------------------------------------------------------------------------

_RUNNER_HEALTH_CACHE_TTL_S: float = float(os.environ.get("RUNNER_HEALTH_CACHE_TTL_S", "5"))
_runner_health_cache: tuple[float, str, str | None, list[str]] | None = None
_runner_health_lock: asyncio.Lock = asyncio.Lock()


class RunnerHealthProbe:
    """Check health of local actions.runner.* systemd units (issue #712)."""

    name = "runner_health"

    async def check(self) -> tuple[ProbeStatus, str | None]:
        """Check runner unit health via systemctl list-units.

        Pre-condition: subprocess timeout <= 5s (enforced via asyncio.wait_for).
        Post-condition: always returns (status, detail) with detail never None on non-ok.
        """
        global _runner_health_cache

        async with _runner_health_lock:
            now = time.monotonic()
            if _runner_health_cache is not None:
                cached_at, status, detail, _failed = _runner_health_cache
                if now - cached_at < _RUNNER_HEALTH_CACHE_TTL_S:
                    return status, detail  # type: ignore[return-value]

            try:
                result = await asyncio.wait_for(
                    _query_runner_units(),
                    timeout=5.0,
                )
                total, active, failed_units = result
            except asyncio.TimeoutError:
                log.warning("runner_health probe timed out")
                _runner_health_cache = (now, "degraded", "systemctl probe timed out", [])
                return "degraded", "systemctl probe timed out"
            except Exception as exc:  # noqa: BLE001
                log.warning("runner_health probe error: %s", exc)
                detail = f"probe error: {exc}"
                _runner_health_cache = (now, "degraded", detail, [])
                return "degraded", detail

            failed_count = len(failed_units)

            if failed_count == 0:
                status: ProbeStatus = "ok"
                detail = None
            elif total > 0 and failed_count <= math.ceil(total * 0.1):
                status = "degraded"
                detail = f"{failed_count}/{total} runners failed: {failed_units}"
            else:
                status = "down"
                detail = f"{failed_count}/{total} runners failed (>10%): {failed_units}"

            _runner_health_cache = (now, status, detail, failed_units)
            return status, detail


async def _query_runner_units() -> tuple[int, int, list[str]]:
    """Query systemctl for actions.runner.* unit states.

    Returns (total, active, failed_unit_names).
    """
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        "list-units",
        "--all",
        "--plain",
        "--no-legend",
        "actions.runner.*",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    lines = stdout.decode().splitlines()

    total = 0
    active = 0
    failed: list[str] = []

    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        unit_name = parts[0]
        active_state = parts[2] if len(parts) > 2 else ""
        total += 1
        if active_state == "active":
            active += 1
        elif active_state == "failed":
            failed.append(unit_name)

    return total, active, failed


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

_DEFAULT_PROBES: list[Probe] = [
    GhTokenProbe(),
    GhCliProbe(),
    LeaseDbProbe(),
    PushDbProbe(),
    RunnerHealthProbe(),
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
