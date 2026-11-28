"""Quota auto-restore tick for shared provider markers (#4564 slice 2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
for _p in (str(_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from coordination import provider_down_markers as pdm  # noqa: E402
from coordination import provider_marker_restore as restore  # noqa: E402


def test_restore_clears_expired_and_recovered_with_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    base = 5_000_000.0
    tick = base + 60.0
    pdm.set_marker("codex", "rate_limited", until=base + 10, now=base)
    pdm.set_marker("claude", "provider_down", until=tick + 600, now=base)
    pdm.set_marker("cursor", "provider_down", until=tick + 600, now=base)

    audits: list[dict] = []
    result = restore.run_provider_marker_restore_tick(
        now=tick,
        provider_healthy=lambda prov: prov == "claude",
        audit=audits.append,
    )
    assert result["store_ok"] is True
    assert {row["provider"] for row in result["cleared_expired"]} == {"codex"}
    assert {row["provider"] for row in result["cleared_recovered"]} == {"claude"}
    assert len(audits) == 1
    assert audits[0]["cleared_total"] == 2
    remaining = {m["provider"] for m in pdm.list_active_markers(now=tick)}
    assert remaining == {"cursor"}


def test_restore_unreadable_store_fails_open_no_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    pdm.state_path().write_text("{bad", encoding="utf-8")
    audits: list[dict] = []
    result = restore.run_provider_marker_restore_tick(now=1.0, audit=audits.append)
    assert result["store_ok"] is False
    assert result["cleared_expired"] == []
    assert result["cleared_recovered"] == []
    assert audits == []


def test_restore_idempotent_when_nothing_to_clear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    now = 6_000_000.0
    pdm.set_marker("codex", "provider_down", until=now + 90, now=now)
    audits: list[dict] = []

    first = restore.run_provider_marker_restore_tick(now=now, provider_healthy=lambda _: False, audit=audits.append)
    second = restore.run_provider_marker_restore_tick(now=now, provider_healthy=lambda _: False, audit=audits.append)
    assert first["cleared_total"] == 0
    assert second["cleared_total"] == 0
    assert audits == []
