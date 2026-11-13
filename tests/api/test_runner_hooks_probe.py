"""Tests for RunnerHooksProbe (#4046)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import readiness as r  # noqa: E402
import runner_hooks_probe as hooks  # noqa: E402


@pytest.mark.asyncio
async def test_runner_hooks_probe_ok_without_runner_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    restore = tmp_path / "runner-hooks-restore"
    restore.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    restore.chmod(0o755)
    monkeypatch.setattr(
        hooks.RunnerHooksProbe,
        "_check_sync",
        lambda self: ("ok", None),
    )
    probe = hooks.RunnerHooksProbe(restore_bin=restore, cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "ok"
    assert detail is None


@pytest.mark.asyncio
async def test_runner_hooks_probe_degraded_on_check_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    restore = tmp_path / "runner-hooks-restore"
    restore.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    restore.chmod(0o755)

    def fake_check(self: hooks.RunnerHooksProbe) -> tuple[hooks.ProbeStatus, str | None]:
        return "degraded", "hook drift"

    monkeypatch.setattr(hooks.RunnerHooksProbe, "_check_sync", fake_check)
    probe = hooks.RunnerHooksProbe(restore_bin=restore, cache_ttl_seconds=0)
    status, detail = await probe.check()
    assert status == "degraded"
    assert detail == "hook drift"


def test_runner_hooks_probe_in_default_probes() -> None:
    names = [p.name for p in r.get_default_probes()]
    assert "runner_hooks" in names
