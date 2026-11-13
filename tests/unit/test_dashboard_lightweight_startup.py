"""DASHBOARD_LIGHTWEIGHT_STARTUP keeps CI/E2E backend startup off leader paths."""

from __future__ import annotations

import importlib
import os

import pytest


def test_lightweight_startup_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_LIGHTWEIGHT_STARTUP", raising=False)
    import dashboard_config

    importlib.reload(dashboard_config)
    assert dashboard_config.LIGHTWEIGHT_STARTUP is False


def test_lightweight_startup_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_LIGHTWEIGHT_STARTUP", "1")
    import dashboard_config

    importlib.reload(dashboard_config)
    assert dashboard_config.LIGHTWEIGHT_STARTUP is True


def test_server_startup_skips_leader_when_lightweight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_LIGHTWEIGHT_STARTUP", "1")
    import dashboard_config

    importlib.reload(dashboard_config)
    import server

    importlib.reload(server)

    audit_started = False
    scheduler_started = False

    def _mark_audit() -> None:
        nonlocal audit_started
        audit_started = True

    def _mark_scheduler() -> None:
        nonlocal scheduler_started
        scheduler_started = True

    monkeypatch.setattr(server._runner_audit_router, "start_audit_loop", _mark_audit)
    monkeypatch.setattr(server._staff_schedule_router, "start_scheduler", _mark_scheduler)

    import asyncio

    asyncio.run(server._startup())

    assert audit_started is False
    assert scheduler_started is False
