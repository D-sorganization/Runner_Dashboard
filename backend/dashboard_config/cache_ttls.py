"""Named constants for cache TTL values used across the dashboard backend.

The frontend polls dashboard endpoints every 10-15 seconds. Without caching,
each poll fans out into dozens of ``gh api`` subprocesses that rapidly exhaust
the 5,000 req/hr GitHub API rate limit. These TTLs are tuned per endpoint to
balance freshness against rate-limit headroom and operator perception of
staleness.

All values are in seconds and consumed via ``cache_utils._cache_get`` /
``cache_utils.cache_set``.
"""

from __future__ import annotations


class CacheTtl:
    """Cache TTL values in seconds, grouped by endpoint family.

    Each constant is named with a ``_S`` suffix to make the unit explicit at
    call sites. Values are tuned empirically — bumping them higher reduces
    GitHub API load but makes the dashboard feel staler.
    """

    # Runner state changes the moment a job starts or finishes; operators
    # expect near-real-time updates. 25 s keeps poll cost bounded while still
    # feeling live.
    RUNNERS_S: int = 25

    # Queue, local-app health, workflow stats, CI test results: 2 minutes is
    # the operator perception threshold for "stale" data on these dashboards.
    QUEUE_S: int = 120
    LOCAL_APPS_S: int = 120
    STATS_S: int = 120
    CI_TEST_RESULTS_S: int = 120

    # Usage monitoring aggregates expensive multi-source data; 5 minutes is
    # acceptable because the underlying inputs only change on that cadence.
    USAGE_MONITORING_S: int = 300

    # Repository inventory rarely changes (new repos, archive flips). 10
    # minutes is plenty fresh and keeps the org-wide enrichment scan cheap.
    REPOS_S: int = 600

    # Watchdog cache: 30 seconds gives operators a near-live deployment
    # health pulse without spawning systemctl on every poll.
    WATCHDOG_S: int = 30

    # Runner-capacity snapshot is embedded in every /api/system and
    # /api/fleet/status response. Building it forks the runner-scheduler binary
    # (``--dry-run --json``) plus two ``systemctl is-active`` calls — ~2-3 s of
    # blocking subprocess work on a busy WSL host. Those calls dominated the
    # endpoint latency and pushed /api/fleet/status past its 15 s budget (504).
    # The underlying schedule/timer state changes on the order of minutes, so a
    # 15 s cache keeps the panel effectively live while collapsing the per-poll
    # fork cost to near zero.
    RUNNER_CAPACITY_S: int = 15

    # Credentials probe cache: 15 s balances near-live status updates against
    # repeated subprocess forks (gh auth status, etc.) from fast frontend polling.
    CREDENTIALS_S: int = 15
