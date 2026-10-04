"""Tests for RunnerPipCacheProbe (issue #1896).

Verifies:
- Probe returns "ok" when all runner pip caches are writable.
- Probe returns "down" with details when any runner pip cache is unwritable.
- Probe returns "ok" when no runner directories are found.
- Probe respects cache TTL.
- Probe is included in get_default_probes().
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import readiness as r  # noqa: E402


@pytest.mark.asyncio
async def test_pip_cache_probe_ok_when_no_runners(tmp_path: Path) -> None:
    """When no runner directories exist, probe returns ok."""
    probe = r.RunnerPipCacheProbe(runner_root=tmp_path, cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_pip_cache_probe_ok_when_all_caches_writable(tmp_path: Path) -> None:
    """When runner pip caches are writable, probe returns ok."""
    r1 = tmp_path / "runner-1"
    r1.mkdir()
    (r1 / "bin").mkdir()
    c1 = r1 / "_work" / "_pip-cache"
    c1.mkdir(parents=True)

    r2 = tmp_path / "runner-2"
    r2.mkdir()
    (r2 / "bin").mkdir()
    custom_cache = tmp_path / "custom-cache"
    custom_cache.mkdir()
    (r2 / ".env").write_text(f"PIP_CACHE_DIR={custom_cache}\n", encoding="utf-8")

    probe = r.RunnerPipCacheProbe(runner_root=tmp_path, cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "ok"


@pytest.mark.asyncio
async def test_pip_cache_probe_down_when_unwritable(tmp_path: Path) -> None:
    """When any runner pip cache is unwritable, probe returns down with details."""
    r1 = tmp_path / "runner-1"
    r1.mkdir()
    (r1 / "bin").mkdir()

    probe = r.RunnerPipCacheProbe(runner_root=tmp_path, cache_ttl_seconds=0)

    # Simulate writability failure on runner-1
    with patch.object(probe, "_check_path_writable", return_value=(False, "Permission denied")):
        status, detail = await probe.check()

    assert status == "down"
    assert detail is not None
    assert "runner-1" in detail
    assert "Permission denied" in detail


@pytest.mark.asyncio
async def test_pip_cache_probe_caches_result(tmp_path: Path) -> None:
    """Rapid polls should be served from cache within TTL."""
    r1 = tmp_path / "runner-1"
    r1.mkdir()
    (r1 / "bin").mkdir()

    probe = r.RunnerPipCacheProbe(runner_root=tmp_path, cache_ttl_seconds=60)
    call_count = 0

    orig_check = probe._check_runner_pip_cache

    def wrapped_check(r_dir: Path) -> tuple[bool, str | None]:
        nonlocal call_count
        call_count += 1
        return orig_check(r_dir)

    probe._check_runner_pip_cache = wrapped_check  # type: ignore[method-assign]

    await probe.check()
    await probe.check()
    await probe.check()

    assert call_count == 1, f"Expected 1 check, got {call_count}"


def test_pip_cache_probe_is_in_default_probes() -> None:
    """RunnerPipCacheProbe must be registered in get_default_probes()."""
    names = {p.name for p in r.get_default_probes()}
    assert "runner_pip_cache" in names
