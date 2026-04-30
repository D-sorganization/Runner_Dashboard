"""Named constants for HTTP timeouts, concurrency caps, and resource thresholds.

These literals were previously sprinkled across ``server.py``,
``queue_cleanup.py``, ``runner_autoscaler.py``, and the routers. Centralising
them gives operators a single place to tune behaviour and gives reviewers a
single place to reason about timing and resource ceilings.

All time values are in seconds unless otherwise noted.
"""

from __future__ import annotations


class HttpTimeout:
    """HTTP / subprocess timeout values in seconds.

    The names describe the call site (Maxwell proxy, hub VERSION fetch,
    systemctl, etc.) so reviewers can see at a glance which subsystem owns a
    given budget.
    """

    # Maxwell daemon proxy calls run on localhost; 3 s is generous for any
    # healthy daemon and lets us fail fast when it is unresponsive.
    MAXWELL_PROXY_S: float = 3.0

    # Hub VERSION fetch + local systemctl invocations: 5 s is enough for the
    # Tailscale-wrapped HTTP round trip or a systemd unit-file lookup.
    HUB_VERSION_FETCH_S: float = 5.0
    SYSTEMCTL_S: int = 5

    # Cross-node system probes go through the Tailscale mesh and may queue
    # behind a busy CPU; 8 s gives the remote dashboard time to respond.
    PROXY_NODE_SYSTEM_S: float = 8.0

    # Hub-bound proxies and the default ``gh api`` budget. 15 s tolerates
    # GitHub API tail latency without holding workers too long.
    PROXY_TO_HUB_S: float = 15.0
    GH_API_DEFAULT_S: int = 15

    # Default budget for ``run_cmd`` subprocess invocations. Used at call
    # sites that pass ``timeout=20`` explicitly when a tighter budget is
    # appropriate (e.g. GitHub API fan-outs). The function's own default is
    # ``GH_DISPATCH_S`` because dispatch is the worst-case caller.
    RUN_CMD_DEFAULT_S: int = 20

    # Workflow / runner dispatch via ``gh`` may negotiate webhooks under load;
    # 30 s matches GitHub's documented dispatch worst case.
    GH_DISPATCH_S: int = 30


class Concurrency:
    """Concurrency caps for fan-out scans and bulk operations.

    Tuned to keep us under GitHub API secondary rate limits while still
    finishing org-wide scans in a few seconds.
    """

    # Stale-queue scan fans out across every repo in the org; 10 keeps us
    # comfortably under GitHub's secondary rate limits.
    QUEUE_SCAN: int = 10

    # Bulk cancel fan-out: 5 concurrent cancel calls is a safe ceiling;
    # higher values trigger 403 abuse-detection responses.
    QUEUE_CANCEL: int = 5

    # Repo enrichment in /api/repos batches metadata calls so the dashboard
    # renders quickly without blowing the per-host connection pool.
    # Current batch size is 10 — bump cautiously, GitHub will return 403
    # abuse-detection responses above ~30 concurrent calls per host.
    REPO_ENRICHMENT: int = 10


class ResourceThreshold:
    """Resource pressure thresholds for the autoscaler and disk-warning UI.

    Percent values match the autoscaler's environment-variable defaults so the
    backend, frontend, and runner-autoscaler all agree on what counts as
    "warning" vs. "critical" pressure.
    """

    # Disk usage percent at which the dashboard surfaces a warning banner.
    DISK_WARN_PERCENT: float = 85.0

    # Memory / disk usage percent at which the autoscaler starts shedding
    # runners; also surfaced as a critical banner in the UI.
    MEMORY_CRITICAL_PERCENT: float = 92.0
    DISK_CRITICAL_PERCENT: float = 92.0

    # CPU / disk usage percent at which the autoscaler hard-stops new runner
    # leases regardless of sustain time.
    CPU_HARD_STOP_PERCENT: float = 95.0
    DISK_HARD_STOP_PERCENT: float = 95.0

    # Minimum free disk headroom in gibibytes; below this the autoscaler
    # treats the host as full and refuses to take new jobs.
    DISK_MIN_FREE_GB: float = 25.0
