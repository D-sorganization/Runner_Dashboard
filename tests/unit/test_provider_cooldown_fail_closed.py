"""Unparseable cooldown rows fail closed (#4267)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from staff import provider_cooldown as pc

pytestmark = pytest.mark.unit


def test_unparseable_cooldown_until_treated_as_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_PROVIDER_COOLDOWNS_FILE", str(tmp_path / "cooldowns.json"))
    path = tmp_path / "cooldowns.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cooldowns": {
                    "codex": {"failure_class": "rate_limited", "until": "not-a-timestamp", "reason": "x"}
                },
            }
        ),
        encoding="utf-8",
    )
    pc.reload_provider_cooldowns()
    assert pc.provider_in_cooldown("codex")
