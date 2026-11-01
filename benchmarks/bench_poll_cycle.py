#!/usr/bin/env python3
"""Poll-cycle benchmark: what do the endpoints the dashboard UI polls cost?

Runs the real FastAPI app in-process with stubbed GitHub REST and a fake ``gh`` (no
network, no real token). Per endpoint, cold and warm: wall time, GitHub HTTP calls,
``gh`` / other subprocess spawns and the longest event-loop stall. Indicative only;
see benchmarks/README.md. Run: python -m benchmarks.bench_poll_cycle --help
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "backend")]

from benchmarks.poll_cycle import fake_gh  # noqa: E402
from benchmarks.poll_cycle.metrics import (  # noqa: E402
    MS_PER_S,
    EndpointResult,
    LagMonitor,
    PhaseResult,
    SpawnCounter,
    format_table,
)
from benchmarks.poll_cycle.stub_github import StubGitHub  # noqa: E402

DEFAULT_LATENCY_S = 0.15
SETTLE_S = 0.05
DEFAULT_ENDPOINTS = tuple(
    "/api/health /api/runners /api/system /api/fleet/status /api/queue "
    "/api/queue/stale /api/runs/enriched /api/v1/staff/board".split()
)
FAKE_TOKEN = "stub-not-a-real-token"  # not a credential; only ever set in os.environ at runtime
FAKE_API_KEY = "bench-not-a-real-key"  # pragma: allowlist secret (stub; auth is overridden)


def configure_environment(latency_s: float, mode: str, work_dir: Path) -> None:
    """Set the env vars the backend reads at import time; must run before importing it."""
    os.environ["RUNNER_DASHBOARD_CONFIG_DIR"] = tempfile.mkdtemp(prefix="poll-bench-")
    os.environ.setdefault("DASHBOARD_TRUSTED_EXTRA_HOSTS", "testserver,test")
    os.environ.update(
        AUTODERIVE_FLEET_NODES="0", FLEET_NODES="", STAFF_MOCK_INSTALLED="1",
        DASHBOARD_API_KEY=FAKE_API_KEY,
    )  # fmt: skip
    bin_dir = fake_gh.install_fake_gh(work_dir / "bin", REPO_ROOT, latency_s).parent
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_APP_ID"):
        os.environ.pop(var, None)
    if mode == "token":
        os.environ["GH_TOKEN"] = FAKE_TOKEN


def load_app(stub: StubGitHub) -> tuple[Any, Any]:
    """Import the backend, bypass auth and point its GitHub client at the stub."""
    import gh_client
    import server
    from cache_utils import cache_clear
    from identity import Principal, require_principal

    logging.disable(logging.INFO)  # the app logs every request; keep stdout to the table
    principal = Principal(id="bench-admin", type="bot", name="Bench", roles=["admin"])
    server.app.dependency_overrides[require_principal] = lambda: principal
    stub_transport = httpx.MockTransport(stub.handler)
    gh_client._client = httpx.AsyncClient(base_url="https://api.github.com", transport=stub_transport)
    from http_clients import initialize_http_clients

    initialize_http_clients()
    return server.app, cache_clear


async def measure(client: httpx.AsyncClient, endpoint: str, probes: SimpleNamespace) -> PhaseResult:
    """GET ``endpoint`` once and return what it cost."""
    probes.counter.by_name.clear()
    probes.monitor.max_lag_s = 0.0
    calls_before = probes.stub.calls
    started = time.perf_counter()
    try:
        status: int | str = (await client.get(endpoint)).status_code
    except httpx.HTTPError as exc:
        status = f"ERR {type(exc).__name__}"
    elapsed = time.perf_counter() - started
    await asyncio.sleep(SETTLE_S)  # let fire-and-forget work show up in the counters
    http_calls, stall_ms = probes.stub.calls - calls_before, round(probes.monitor.max_lag_s * MS_PER_S)
    counter = probes.counter
    return PhaseResult(status, round(elapsed * MS_PER_S), http_calls, counter.gh, counter.other, stall_ms)


async def run_benchmark(latency_s: float, endpoints: list[str]) -> list[EndpointResult]:
    """Hit each endpoint cold then warm and return the measurements."""
    stub = StubGitHub(latency_s)
    app, cache_clear = load_app(stub)
    probes, results = SimpleNamespace(stub=stub, counter=SpawnCounter(), monitor=LagMonitor()), []
    headers = {"Authorization": f"Bearer {FAKE_API_KEY}", "X-Requested-With": "XMLHttpRequest"}
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)  # 500s are data, not crashes
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=headers) as client:
        await client.get("/api/health")  # absorb lazy-import cost
        probes.monitor.start()
        with probes.counter.installed():
            for endpoint in endpoints:
                cache_clear()
                cold = await measure(client, endpoint, probes)
                warm = await measure(client, endpoint, probes)
                results.append(EndpointResult(endpoint, cold, warm))
        await probes.monitor.stop()
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--latency", type=float, default=DEFAULT_LATENCY_S, help="stub GitHub latency (s)")
    parser.add_argument("--mode", choices=("token", "gh"), default="token", help="GitHub auth path to exercise")
    parser.add_argument("--endpoint", action="append", help="endpoint to poll; repeatable (default: key polls)")
    parser.add_argument("--json-out", type=Path, help="write results as JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    endpoints = args.endpoint or list(DEFAULT_ENDPOINTS)
    with tempfile.TemporaryDirectory(prefix="poll-bench-gh-") as work_dir:
        configure_environment(args.latency, args.mode, Path(work_dir))
        results = asyncio.run(run_benchmark(args.latency, endpoints))
    print(format_table(results))
    if args.json_out:
        payload = [dataclasses.asdict(r) for r in results]
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
