"""Unparseable cooldown rows fail closed (#4267)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from staff import provider_cooldown as pc

pytestmark = pytest.mark.unit


def test_unparseable_cooldown_until_treated_as_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_PROVIDER_COOLDOWNS_FILE", str(tmp_path / "cooldowns.json"))
    path = tmp_path / "cooldowns.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cooldowns": {"codex": {"failure_class": "rate_limited", "until": "not-a-timestamp", "reason": "x"}},
            }
        ),
        encoding="utf-8",
    )
    pc.reload_provider_cooldowns()
    assert pc.provider_in_cooldown("codex")


@pytest.mark.parametrize("invalid_until", ["not-a-timestamp", None, ""])
def test_malformed_cooldown_keeps_original_deadline_across_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_until: str | None
) -> None:
    """A repaired expiry survives restart without extending its one-hour window."""
    path = tmp_path / "cooldowns.json"
    monkeypatch.setenv("STAFF_PROVIDER_COOLDOWNS_FILE", str(path))
    clock = [datetime(2026, 11, 16, 12, tzinfo=UTC)]
    monkeypatch.setattr(pc, "time", SimpleNamespace(time=lambda: clock[0].timestamp()))
    monkeypatch.setattr(pc, "datetime", SimpleNamespace(now=lambda _tz: clock[0], fromisoformat=datetime.fromisoformat))
    path.write_text(
        json.dumps({"cooldowns": {"codex": {"failure_class": "rate_limited", "until": invalid_until}}}),
        encoding="utf-8",
    )
    pc.reload_provider_cooldowns()
    assert pc.provider_in_cooldown("codex")

    deadline = clock[0] + timedelta(hours=1)
    clock[0] += timedelta(minutes=10)
    pc.reload_provider_cooldowns()
    assert pc.provider_in_cooldown("codex")
    assert pc.provider_cooldown_until("codex") == deadline.isoformat().replace("+00:00", "Z")

    clock[0] = deadline + timedelta(seconds=1)
    pc.reload_provider_cooldowns()
    assert not pc.provider_in_cooldown("codex")
