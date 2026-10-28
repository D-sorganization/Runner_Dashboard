"""Provider cooldown store and summary exposure (#2047 slice 2) + durability."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest
from staff import provider_cooldown
from staff.provider_cooldown import (
    cooldown_file_path,
    cooldown_snapshot,
    mark_provider_cooldown,
    provider_cooldown_until,
    provider_in_cooldown,
    reload_provider_cooldowns,
    reset_provider_cooldowns,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_provider_cooldowns()


def test_cooldown_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    # Long window + injected clock: no real sleep, immune to slow disks (#2909).
    mark_provider_cooldown("codex", "provider_capacity", message="retry after 600 seconds")
    assert provider_in_cooldown("codex")
    until_epoch = datetime.fromisoformat(str(provider_cooldown_until("codex")).replace("Z", "+00:00")).timestamp()
    monkeypatch.setattr(provider_cooldown, "time", SimpleNamespace(time=lambda: until_epoch + 1.0))
    assert not provider_in_cooldown("codex")


def test_cooldown_snapshot_serializable() -> None:
    mark_provider_cooldown("claude", "quota_exhausted")
    snap = cooldown_snapshot()
    assert snap and snap[0]["provider"] == "claude"
    json.dumps(snap)


# --- durable cooldowns (survive hub restart) --------------------------------


def test_cooldown_survives_restart() -> None:
    until = mark_provider_cooldown("codex", "quota_exhausted", message="retry after 600 seconds")
    before = provider_cooldown_until("codex")
    reload_provider_cooldowns()  # simulate hub restart: memory dropped, disk re-read
    assert provider_in_cooldown("codex")
    assert provider_cooldown_until("codex") == before
    assert until.tzinfo is not None
    snap = cooldown_snapshot()
    assert snap[0]["provider"] == "codex"
    assert snap[0]["failure_class"] == "quota_exhausted"


def test_expired_entries_pruned_on_load() -> None:
    path = cooldown_file_path()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cooldowns": {
                    "old": {"failure_class": "rate_limited", "until": "2000-01-01T00:00:00Z", "reason": "x"},
                    "new": {"failure_class": "rate_limited", "until": "2999-01-01T00:00:00Z", "reason": "y"},
                },
            }
        ),
        encoding="utf-8",
    )
    reload_provider_cooldowns()
    assert not provider_in_cooldown("old")
    assert provider_in_cooldown("new")
    assert "old" not in json.loads(path.read_text(encoding="utf-8"))["cooldowns"]


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", '{"cooldowns": 5}', '{"cooldowns": {"a": {"until": "bad"}}}'],
)
def test_corrupt_file_loads_empty(content: str) -> None:
    cooldown_file_path().write_text(content, encoding="utf-8")
    reload_provider_cooldowns()
    assert cooldown_snapshot() == []
    mark_provider_cooldown("codex", "provider_capacity")  # still writable afterwards
    assert provider_in_cooldown("codex")


def test_missing_file_loads_empty() -> None:
    cooldown_file_path().unlink(missing_ok=True)
    reload_provider_cooldowns()
    assert cooldown_snapshot() == []


def test_concurrent_marks_do_not_corrupt_file() -> None:
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            for _ in range(20):
                mark_provider_cooldown(f"p{i}", "rate_limited")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    data = json.loads(cooldown_file_path().read_text(encoding="utf-8"))
    assert set(data["cooldowns"]) == {f"p{i}" for i in range(8)}
    reload_provider_cooldowns()
    assert all(provider_in_cooldown(f"p{i}") for i in range(8))


def test_reset_clears_file() -> None:
    mark_provider_cooldown("codex", "provider_capacity")
    reset_provider_cooldowns()
    reload_provider_cooldowns()
    assert not provider_in_cooldown("codex")
