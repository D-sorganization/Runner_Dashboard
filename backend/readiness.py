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
import json
import logging
import os
import shutil
import time
from typing import Final, Literal, Protocol, runtime_checkable

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
    """Check that GitHub credentials are present in the environment.

    We do not make a live GitHub API call here — that would introduce I/O
    latency and a GitHub-outage → restart-loop regression (#332).  The probe
    only verifies credentials are loaded; actual API reachability is surfaced via
    ``/api/health`` (the human-readable composite view).
    """

    name = "github_token"

    async def check(self) -> tuple[ProbeStatus, str | None]:
        app_id = os.environ.get("GITHUB_APP_ID", "").strip()
        installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID", "").strip()
        private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
        private_key_file = os.environ.get("GITHUB_APP_PRIVATE_KEY_FILE", "").strip()
        if app_id and installation_id and (private_key or private_key_file):
            return "ok", "GitHub App auth configured"

        token = os.environ.get("GH_TOKEN", "").strip()
        if token:
            return "ok", None
        return "down", "GitHub App auth or GH_TOKEN env var not set"


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


class RunnerHealthProbe:
    """Surface failed local `actions.runner.*` systemd units in /readyz (A6).

    Without this probe, the dashboard can report `/readyz` = 200 while every
    local runner unit is in `failed` state — operators have no programmatic
    signal of fleet degradation. See findings H1/H2 of the runner-stability
    audit.

    The probe is **best-effort**: machines without systemd (WSL dev hosts,
    docker, macOS) report `degraded` instead of `down`, because the dashboard
    itself is still healthy — it just cannot observe local runners on that
    platform.

    Status mapping
    --------------
        no `actions.runner.*` units present                → ok
        all units active, none failed                      → ok
        0 < failed_units ≤ critical_failure_pct × total    → degraded
        failed_units > critical_failure_pct × total        → down
        subprocess timeout or systemctl missing            → degraded

    Caching
    -------
    The probe caches its last result for ``cache_ttl_seconds`` so that frequent
    `/readyz` polls do not fork-bomb ``systemctl``. Set ``cache_ttl_seconds=0``
    in tests to disable caching.
    """

    # `name` is a settable attribute (not Final) so the class satisfies the
    # `Probe` Protocol, which declares `name` as a writable variable.
    name = "runner_health"

    _SYSTEMCTL_ARGS: Final[tuple[str, ...]] = (
        "list-units",
        "actions.runner.*",
        "--all",
        "--no-legend",
        "--output=json",
    )

    def __init__(
        self,
        cache_ttl_seconds: float = 5.0,
        subprocess_timeout_seconds: float = 5.0,
        critical_failure_pct: float = 0.10,
        systemctl_bin: str = "systemctl",
    ) -> None:
        # Pre-conditions (DbC): probe configuration must be sane.
        assert cache_ttl_seconds >= 0.0, "cache_ttl_seconds must be ≥ 0"
        assert subprocess_timeout_seconds > 0.0, "subprocess_timeout_seconds must be > 0"
        assert 0.0 < critical_failure_pct < 1.0, "critical_failure_pct must be in (0, 1)"

        self._cache_ttl = cache_ttl_seconds
        self._subprocess_timeout = subprocess_timeout_seconds
        self._critical_pct = critical_failure_pct
        self._systemctl_bin = systemctl_bin
        self._cached: tuple[float, ProbeStatus, str | None] | None = None

    async def check(self) -> tuple[ProbeStatus, str | None]:
        # Serve from cache when TTL is still valid.
        if self._cache_ttl > 0 and self._cached is not None:
            ts, status, detail = self._cached
            if (time.monotonic() - ts) < self._cache_ttl:
                return status, detail

        status, detail = await self._probe_systemd()
        self._cached = (time.monotonic(), status, detail)
        return status, detail

    async def _probe_systemd(self) -> tuple[ProbeStatus, str | None]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._systemctl_bin,
                *self._SYSTEMCTL_ARGS,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            # Dev/non-systemd hosts. Not a hard failure of the dashboard.
            return "degraded", f"systemctl not available on this host ({self._systemctl_bin!r} not found)"
        except OSError as exc:
            return "degraded", f"systemctl could not start: {exc}"

        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=self._subprocess_timeout)
        except TimeoutError:
            # Best-effort kill so we don't leak a process. Any failure here
            # (already-dead PID, no kill method, EPERM, etc.) is irrelevant —
            # the probe outcome is "degraded" regardless.
            try:
                kill = getattr(proc, "kill", None)
                if callable(kill):
                    kill()
            except Exception:  # noqa: BLE001
                pass
            return "degraded", f"systemctl list-units timeout after {self._subprocess_timeout}s"

        return self._classify_output(stdout)

    def _classify_output(self, stdout: bytes) -> tuple[ProbeStatus, str | None]:
        try:
            payload = json.loads(stdout.decode("utf-8") or "[]")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return "degraded", f"systemctl output not valid JSON: {exc}"

        if not isinstance(payload, list):
            return "degraded", "systemctl output was not a JSON array"

        total = len(payload)
        if total == 0:
            # Nodes without local runners (e.g., dashboard-only hub) are healthy.
            return "ok", None

        failed_units: list[str] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            # systemctl reports failed units with active == "failed".
            if str(entry.get("active", "")).lower() == "failed":
                unit_name = str(entry.get("unit", "")) or "<unknown>"
                failed_units.append(unit_name)

        if not failed_units:
            return "ok", None

        # Post-condition: status reflects the failed-unit ratio per the
        # configured threshold.
        pct = len(failed_units) / total
        summary = f"{len(failed_units)}/{total} runner units failed: {', '.join(failed_units[:5])}"
        if len(failed_units) > 5:
            summary += f" (+{len(failed_units) - 5} more)"

        if pct > self._critical_pct:
            return "down", summary
        return "degraded", summary


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
