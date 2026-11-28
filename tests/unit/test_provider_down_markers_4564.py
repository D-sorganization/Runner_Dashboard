"""Provider-down / rate-limited markers persisted in RD state (#4564 slice 1)."""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
for _p in (str(_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from coordination import provider_down_markers as pdm  # noqa: E402


def test_set_read_clear_and_expiry_prunes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    now = 1_000_000.0
    pdm.set_marker("claude", "rate_limited", until=now + 60, now=now, reason="429 burst")
    pdm.set_marker("codex", "provider_down", until=now + 300, now=now)

    active = pdm.list_active_markers(now=now + 10)
    providers = {m["provider"] for m in active}
    assert providers == {"claude", "codex"}
    assert active[0]["kind"] in ("provider_down", "rate_limited")

    pdm.clear_marker("claude")
    assert {m["provider"] for m in pdm.list_active_markers(now=now + 10)} == {"codex"}

    # Expired marker is dropped on read and persisted file is rewritten without it.
    pdm.set_marker("cursor", "provider_down", until=now + 5, now=now)
    assert {m["provider"] for m in pdm.list_active_markers(now=now + 100)} == {"codex"}
    raw = json.loads(pdm.state_path().read_text(encoding="utf-8"))
    providers_on_disk = {m["provider"] for m in raw.get("markers", [])}
    assert "cursor" not in providers_on_disk


def test_unreadable_store_fails_open_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    path = pdm.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        down = pdm.providers_marked_down(now=1.0)
    assert down == set()
    assert "fail" in caplog.text.lower() or "unreadable" in caplog.text.lower()


def test_resolve_failover_skips_marked_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    now = 2_000_000.0
    pdm.set_marker("codex", "provider_down", until=now + 120, now=now)
    assert pdm.resolve_lane_provider_with_failover("codex", now=now) == "claude"
    assert pdm.resolve_lane_provider_with_failover("cursor-agent", now=now) == "cursor"


def test_resolve_all_down_returns_none_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    now = 3_000_000.0
    for prov in pdm.LANE_CHECKOUT_PROVIDER_CHAIN:
        pdm.set_marker(prov, "provider_down", until=now + 60, now=now)
    assert pdm.resolve_lane_provider_with_failover("codex", now=now) is None


def test_invalid_kind_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="kind"):
        pdm.set_marker("claude", "quota_exhausted", until=1.0, now=0.0)


def _pause_first_load(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event]:
    """Block inside the first ``_load_raw`` so a second writer can try to interleave."""
    entered = threading.Event()
    release = threading.Event()
    original = pdm._load_raw
    paused = {"done": False}

    def slow_load() -> tuple[list[pdm.ProviderMarker], bool]:
        result = original()
        if not paused["done"]:
            paused["done"] = True
            entered.set()
            assert release.wait(3), "store lock was not released for the interleaved writer"
        return result

    monkeypatch.setattr(pdm, "_load_raw", slow_load)
    return entered, release


def test_interleaved_set_marker_keeps_both_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lost updates drop a marker when read-modify-write is not one critical section."""
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    now = 4_000_000.0
    entered, release = _pause_first_load(monkeypatch)
    errors: list[BaseException] = []

    def write(provider: str) -> None:
        try:
            pdm.set_marker(provider, "provider_down", until=now + 120, now=now)
        except BaseException as exc:  # noqa: BLE001 - surface worker failures in the assertion
            errors.append(exc)

    first = threading.Thread(target=write, args=("cursor",))
    second = threading.Thread(target=write, args=("codex",))
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        second.join(0.5)
        assert second.is_alive(), "second set_marker ran during the first writer's read-modify-write"
    finally:
        release.set()
        first.join(3)
        second.join(3)
    assert errors == []
    assert {m["provider"] for m in pdm.list_active_markers(now=now)} == {"cursor", "codex"}


def test_prune_during_set_marker_keeps_the_new_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Expiry pruning must not persist a snapshot that omits a marker written concurrently."""
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    now = 5_000_000.0
    pdm.set_marker("claude", "provider_down", until=now - 1, now=now - 30)
    entered, release = _pause_first_load(monkeypatch)
    errors: list[BaseException] = []

    def prune() -> None:
        try:
            pdm.list_active_markers(now=now)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def write() -> None:
        try:
            pdm.set_marker("cursor", "provider_down", until=now + 120, now=now)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=prune)
    second = threading.Thread(target=write)
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        second.join(0.5)
        assert second.is_alive(), "set_marker ran during expiry pruning"
    finally:
        release.set()
        first.join(3)
        second.join(3)
    assert errors == []
    assert {m["provider"] for m in pdm.list_active_markers(now=now)} == {"cursor"}
