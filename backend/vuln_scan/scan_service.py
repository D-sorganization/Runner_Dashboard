"""Wiring between the scan routes and :class:`vuln_scan.executor.ScanExecutor` (#2572).

This module supplies the real probes:

- default-branch head SHA and contract status from GitHub (cached)
- CI queue depth from the Queue tab's aggregation (``routers.queue``)
- scan-host free disk over SSH (``df`` on ``/var/lib/docker``)
- the subscription login check used by staff runs, and the pinned CLI version

The executor runs on a worker thread; async probes are bridged onto the
server's event loop. Only one scan runs at a time (:mod:`vuln_scan.scans`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import threading
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar
from urllib.parse import urlsplit

from dashboard_config import ORG
from fastapi import HTTPException
from security import safe_subprocess_env
from vuln_scan import launch, schedule, service
from vuln_scan.executor import Probes, ScanExecutor, ScanRequest, codeload_fetch
from vuln_scan.findings import default_db_path, get_findings_store
from vuln_scan.operator_config import ScanHost, load_operator_config
from vuln_scan.sandbox import ExecResult, subprocess_runner
from vuln_scan.scans import ScanLog, get_scan_log

log = logging.getLogger("dashboard.vuln_scan.scan_service")

T = TypeVar("T")
_active: dict[str, ScanExecutor] = {}
_active_lock = threading.Lock()


async def default_branch_sha(repo: str) -> str | None:
    """Head SHA of the repository's default branch, or ``None`` on any failure."""
    fetch = service._fetch()
    try:
        meta = await fetch(f"/repos/{ORG}/{repo}")
        branch = meta.get("default_branch") if isinstance(meta, dict) else None
        if not isinstance(branch, str):
            return None
        commit = await fetch(f"/repos/{ORG}/{repo}/commits/{branch}")
    except HTTPException as exc:
        log.warning("default branch lookup failed for %s: %s", repo, exc.detail)
        return None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    return sha if isinstance(sha, str) and len(sha) == 40 else None


QUEUE_MAX_AGE_S = 180.0


def queue_count(payload: dict[str, Any], now: datetime) -> int | None:
    """Queued jobs from a queue payload; ``None`` (not idle) unless complete and fresh.

    The Queue tab serves cached partial and long-stale payloads so its view never
    goes blank (#641). CI yield must not trust those (panel q5): a partial count
    can miss a repository's queue, and a stale one can be hours old.
    """
    stats = payload.get("stats") or {}
    if stats.get("complete") is not True:
        return None
    try:
        generated = datetime.fromisoformat(
            str(payload.get("generated_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    age = (now - generated).total_seconds()
    if age < -60 or age > QUEUE_MAX_AGE_S:
        return None
    count = payload.get("queued_jobs_count")
    return count if isinstance(count, int) and count >= 0 else None


async def queued_ci_jobs() -> int | None:
    """Queued CI jobs across the org; ``None`` (not idle) if partial or stale."""
    from routers.queue import _queue_impl

    return queue_count(await _queue_impl(), datetime.now(UTC))


def _ssh(host: ScanHost, *remote: str) -> ExecResult | None:
    """Run a fixed argv on an ``ssh://`` scan host; ``None`` if it has no SSH target."""
    if not host.docker_host or not host.docker_host.startswith("ssh://"):
        return None
    parts = urlsplit(host.docker_host)
    target = f"{parts.username}@{parts.hostname}" if parts.username else parts.hostname
    if not target:
        return None
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if parts.port:
        argv += ["-p", str(parts.port)]
    return subprocess_runner([*argv, target, *remote], 30, 8192, _host_env())


def _host_env() -> dict[str, str]:
    return {
        k: v
        for k, v in safe_subprocess_env().items()
        if k.upper() in ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT")
    }


def build_filter_ok(host: ScanHost) -> bool:
    """Whether the scan host's phase-A filter is live (panel q5).

    ``setup-build-network.sh --apply`` writes ``/run/vs-build/<network>.ok`` with
    the network id as its last step. ``/run`` is tmpfs, so after a reboot the
    marker is gone until the script runs again; a recreated network has a new id.
    """
    network = host.build_network or ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", network):
        return False
    marker = _ssh(host, "cat", f"/run/vs-build/{network}.ok")
    if marker is None or marker.exit_code != 0:
        return False
    env = {**_host_env(), "DOCKER_HOST": str(host.docker_host)}
    argv = ["docker", "network", "inspect", "--format", "{{.Id}}", network]
    live = subprocess_runner(argv, 60, 8192, env)
    want = marker.stdout.strip()
    return live.exit_code == 0 and bool(want) and live.stdout.strip() == want


def ssh_free_gb(host: ScanHost) -> float | None:
    """Free GB under ``/var/lib/docker`` on an ``ssh://`` scan host."""
    result = _ssh(host, "df", "-Pk", "/var/lib/docker")
    if result is None:
        return None
    lines = result.stdout.strip().splitlines()
    if result.exit_code != 0 or len(lines) < 2:
        return None
    fields = lines[-1].split()
    try:
        return int(fields[3]) / (1024 * 1024)
    except (IndexError, ValueError):
        return None


def claude_cli_version(cli: str) -> str:
    result = subprocess_runner([cli, "--version"], 30, 4096, launch.scan_env(60))
    return result.stdout.strip()


def claude_config_dir() -> str:
    from staff.cli_oauth_session import resolve_claude_config_dir

    return str(resolve_claude_config_dir())


def oauth_ok() -> bool:
    from staff.cli_oauth_session import claude_oauth_session_alive

    alive, _ = claude_oauth_session_alive()
    return alive


def scan_log() -> ScanLog:
    return get_scan_log(default_db_path())


def build_executor(loop: asyncio.AbstractEventLoop) -> ScanExecutor:
    def bridge(coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=120)

    cli = shutil.which("claude") or "claude"
    probes = Probes(
        default_branch_sha=lambda repo: bridge(default_branch_sha(repo)),
        contract=lambda repo: bridge(service.contract_status(repo)),
        queued_jobs=lambda: bridge(queued_ci_jobs()),
        free_gb=ssh_free_gb,
        build_filter_ok=build_filter_ok,
        oauth_ok=oauth_ok,
        cli_version=lambda: claude_cli_version(cli),
        fetch_source=codeload_fetch(ORG),
        now=lambda: datetime.now(UTC),
    )
    return ScanExecutor(
        load_operator_config(),
        probes,
        scan_log(),
        get_findings_store(),
        docker=subprocess_runner,
        cli_path=cli,
        claude_config_dir=claude_config_dir(),
        org=ORG,
    )


async def request_scan(
    repo: str, *, dry_run: bool, requested_by: str
) -> dict[str, Any]:
    """Plan, refuse or start a scan of the default-branch head of ``repo``.

    Real scans run only on the hub, which holds the one fleet-wide slot; every
    node has its own scan log, so a node admitting a scan would be a second slot.
    """
    import dashboard_config

    if not dry_run and dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(
            status_code=409, detail="real scans run only on the hub dashboard"
        )
    sha = await default_branch_sha(repo)
    if sha is None:
        raise HTTPException(
            status_code=502, detail=f"could not resolve {repo}'s default-branch SHA"
        )
    executor = build_executor(asyncio.get_running_loop())
    req = ScanRequest(repo=repo, sha=sha, dry_run=dry_run, requested_by=requested_by)
    return await _submit(executor, req)


async def _submit(executor: ScanExecutor, req: ScanRequest) -> dict[str, Any]:
    """Submit, and when admitted run the scan on a worker thread."""
    row = await asyncio.to_thread(executor.submit, req)
    if row.get("state") == "running":
        scan_id = str(row["id"])
        with _active_lock:
            _active[scan_id] = executor

        def work() -> None:
            try:
                executor.execute(scan_id, req)
            finally:
                with _active_lock:
                    _active.pop(scan_id, None)

        threading.Thread(
            target=work, name=f"security-scan-{scan_id}", daemon=True
        ).start()
    return row


async def coverage_rows() -> list[dict[str, Any]]:
    report = await service.coverage_overview()
    return list(report["coverage"])


async def scheduled_tick() -> dict[str, Any]:
    """One scheduler decision (#2574); see :mod:`vuln_scan.schedule`.

    The config file deploys to every node, so the hub check lives here: only the
    hub holds the fleet-wide slot, and a node never starts a scheduled scan.
    """
    import dashboard_config

    if dashboard_config.MACHINE_ROLE != "hub":
        return {"started": False, "reason": "not the hub"}

    async def plan(
        repo: str,
    ) -> tuple[list[str], Callable[[], Awaitable[dict[str, Any]]]]:
        sha = await default_branch_sha(repo)
        if sha is None:
            return [f"could not resolve {repo}'s default-branch SHA"], _noop
        executor = build_executor(asyncio.get_running_loop())
        req = ScanRequest(repo=repo, sha=sha, dry_run=False, requested_by="scheduler")
        reasons = await asyncio.to_thread(executor.plan, req)
        return reasons, lambda: _submit(executor, req)

    return await schedule.tick(
        config=load_operator_config(),
        slot_free=scan_log().slot_free,
        coverage=coverage_rows,
        plan=plan,
        now=datetime.now(UTC),
    )


async def _noop() -> dict[str, Any]:
    return {}


def start_schedule_loop() -> None:
    """Start the hub's scheduler loop; it does nothing until the owner enables it."""
    asyncio.get_running_loop().create_task(schedule.schedule_loop(scheduled_tick))


def cancel_scan(scan_id: str) -> bool:
    with _active_lock:
        executor = _active.get(scan_id)
    if executor is None:
        return False
    executor.cancelled.set()
    return True
