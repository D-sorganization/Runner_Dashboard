from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.health import wait_for_health  # noqa: E402


def test_health_retry_checks_before_and_after_every_delay() -> None:
    probes: list[str] = []
    sleeps: list[float] = []

    def probe(url: str) -> tuple[bool, str]:
        probes.append(url)
        return len(probes) == 3, "healthy" if len(probes) == 3 else "starting"

    assert wait_for_health(8321, delays=(0.1, 0.2, 0.3), probe=probe, sleep=sleeps.append)
    assert probes == ["http://127.0.0.1:8321"] * 3
    assert sleeps == [0.1, 0.2]


def test_health_success_on_initial_probe_does_not_sleep() -> None:
    probes: list[str] = []
    sleeps: list[float] = []

    def probe(url: str) -> tuple[bool, str]:
        probes.append(url)
        return True, "healthy"

    assert wait_for_health(8321, delays=(0.1, 0.2), probe=probe, sleep=sleeps.append)
    assert len(probes) == 1
    assert sleeps == []


def test_health_retry_fails_after_last_probe() -> None:
    probes: list[str] = []
    sleeps: list[float] = []

    def probe(url: str) -> tuple[bool, str]:
        probes.append(url)
        return False, "starting"

    assert not wait_for_health(8321, delays=(0.1, 0.2), probe=probe, sleep=sleeps.append)
    assert len(probes) == 3
    assert sleeps == [0.1, 0.2]
