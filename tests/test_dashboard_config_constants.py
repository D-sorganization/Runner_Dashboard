"""Smoke tests for the dashboard_config constant submodules.

These tests guard against accidental edits to centrally-defined cache TTLs,
HTTP timeouts, concurrency caps, and resource thresholds. They do not assert
business logic — they only ensure the documented values stay stable, since
operators tune dashboards against these numbers and unexpected drift is hard
to detect at runtime.
"""

from __future__ import annotations


def test_cache_ttls_module_importable() -> None:
    """``CacheTtl`` is importable from the package and the leaf submodule."""
    from dashboard_config import CacheTtl as PackageCacheTtl
    from dashboard_config.cache_ttls import CacheTtl

    assert PackageCacheTtl is CacheTtl


def test_cache_ttl_values() -> None:
    """Cache TTL values match the documented contract."""
    from dashboard_config.cache_ttls import CacheTtl

    assert CacheTtl.RUNNERS_S == 25
    assert CacheTtl.QUEUE_S == 120
    assert CacheTtl.LOCAL_APPS_S == 120
    assert CacheTtl.STATS_S == 120
    assert CacheTtl.CI_TEST_RESULTS_S == 120
    assert CacheTtl.USAGE_MONITORING_S == 300
    assert CacheTtl.REPOS_S == 600
    assert CacheTtl.WATCHDOG_S == 30


def test_cache_ttls_are_positive_integers() -> None:
    """Every cache TTL must be a positive int — `cache_get` expects seconds."""
    from dashboard_config.cache_ttls import CacheTtl

    ttls = {name: getattr(CacheTtl, name) for name in dir(CacheTtl) if name.endswith("_S") and not name.startswith("_")}
    assert ttls, "expected at least one *_S TTL constant"
    for name, value in ttls.items():
        assert isinstance(value, int), f"{name} must be int, got {type(value).__name__}"
        assert value > 0, f"{name} must be positive, got {value}"


def test_http_timeout_module_importable() -> None:
    """``HttpTimeout`` is importable from the package and the leaf submodule."""
    from dashboard_config import HttpTimeout as PackageHttpTimeout
    from dashboard_config.timeouts import HttpTimeout

    assert PackageHttpTimeout is HttpTimeout


def test_http_timeout_values() -> None:
    """HTTP / subprocess timeout values match the documented contract."""
    from dashboard_config.timeouts import HttpTimeout

    assert HttpTimeout.MAXWELL_PROXY_S == 3.0
    assert HttpTimeout.HUB_VERSION_FETCH_S == 5.0
    assert HttpTimeout.SYSTEMCTL_S == 5
    assert HttpTimeout.PROXY_NODE_SYSTEM_S == 8.0
    assert HttpTimeout.PROXY_TO_HUB_S == 15.0
    assert HttpTimeout.GH_API_DEFAULT_S == 15
    assert HttpTimeout.RUN_CMD_DEFAULT_S == 20
    assert HttpTimeout.GH_DISPATCH_S == 30


def test_http_timeouts_are_positive() -> None:
    """Every HTTP timeout must be a positive number."""
    from dashboard_config.timeouts import HttpTimeout

    timeouts = {
        name: getattr(HttpTimeout, name)
        for name in dir(HttpTimeout)
        if name.endswith("_S") and not name.startswith("_")
    }
    assert timeouts, "expected at least one *_S timeout constant"
    for name, value in timeouts.items():
        assert isinstance(value, (int, float)), f"{name} must be numeric, got {type(value).__name__}"
        assert value > 0, f"{name} must be positive, got {value}"


def test_concurrency_caps() -> None:
    """Concurrency caps match the documented contract."""
    from dashboard_config.timeouts import Concurrency

    assert Concurrency.QUEUE_SCAN == 8
    assert Concurrency.QUEUE_CANCEL == 5
    # Repo enrichment kept at the historic batch size of 10.
    assert Concurrency.REPO_ENRICHMENT == 10


def test_resource_thresholds() -> None:
    """Resource pressure thresholds match the documented contract."""
    from dashboard_config.timeouts import ResourceThreshold

    assert ResourceThreshold.DISK_WARN_PERCENT == 85.0
    assert ResourceThreshold.MEMORY_CRITICAL_PERCENT == 92.0
    assert ResourceThreshold.DISK_CRITICAL_PERCENT == 92.0
    assert ResourceThreshold.CPU_HARD_STOP_PERCENT == 95.0
    assert ResourceThreshold.DISK_HARD_STOP_PERCENT == 95.0
    assert ResourceThreshold.DISK_MIN_FREE_GB == 25.0


def test_threshold_ordering_is_monotonic() -> None:
    """Warn < critical < hard-stop — operators rely on this ordering."""
    from dashboard_config.timeouts import ResourceThreshold

    assert ResourceThreshold.DISK_WARN_PERCENT < ResourceThreshold.DISK_CRITICAL_PERCENT
    assert ResourceThreshold.DISK_CRITICAL_PERCENT < ResourceThreshold.DISK_HARD_STOP_PERCENT
