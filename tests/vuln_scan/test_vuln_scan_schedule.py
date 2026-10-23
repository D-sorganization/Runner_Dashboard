"""Coverage report and the owner-enabled scan scheduler (#2574)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from vuln_scan import schedule
from vuln_scan.operator_config import parse_operator_config
from vuln_scan.scans import ScanLog

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
SHA = "0bb24108aa" * 4


def _config(**overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "repos": {
            "Tools": {"enabled": True, "priority": 2, "full_scan_days": 7},
            "UpstreamDrift": {"enabled": True, "priority": 1},
            "AffineDrift": {"enabled": False},
        },
        "schedule": {"enabled": True},
    }
    payload.update(overrides)
    return parse_operator_config(payload)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


CONTRACTS = {
    "Tools": {"present": True, "valid": True},
    "UpstreamDrift": {"present": True, "valid": True},
    "AffineDrift": {"present": True, "valid": True},
    "Beta": {"present": False, "valid": False},
}


def test_coverage_marks_due_repos_most_urgent_first() -> None:
    latest = {
        "Tools": {"last_success_at": _iso(NOW - timedelta(days=8)), "last_state": "succeeded"},
        "UpstreamDrift": {"last_success_at": _iso(NOW - timedelta(days=2)), "last_state": "succeeded"},
    }
    rows = schedule.coverage_rows(_config(), list(CONTRACTS), CONTRACTS, latest, NOW)
    by_repo = {r["repo"]: r for r in rows}
    assert by_repo["Tools"]["due"] is True  # 8 days > full_scan_days 7
    assert by_repo["UpstreamDrift"]["due"] is False  # 2 days < default 30
    assert by_repo["AffineDrift"]["due"] is False  # not enabled
    assert by_repo["Beta"]["due"] is False and by_repo["Beta"]["contract_present"] is False
    assert rows[0]["repo"] == "Tools"
    assert schedule.coverage_summary(rows) == {
        "repos": 4,
        "with_contract": 3,
        "valid": 3,
        "enabled": 2,
        "scanned": 2,
        "due": 1,
    }


def test_never_scanned_enabled_repos_are_due_by_priority() -> None:
    rows = schedule.coverage_rows(_config(), list(CONTRACTS), CONTRACTS, {}, NOW)
    assert [r["repo"] for r in rows if r["due"]] == ["UpstreamDrift", "Tools"]


def test_invalid_contract_is_never_due() -> None:
    contracts = {**CONTRACTS, "Tools": {"present": True, "valid": False}}
    rows = schedule.coverage_rows(_config(), ["Tools"], contracts, {}, NOW)
    assert rows[0]["due"] is False


class Recorder:
    def __init__(self, reasons: list[str] | None = None) -> None:
        self.reasons = reasons or []
        self.planned: list[str] = []
        self.started = 0

    async def plan(self, repo: str) -> tuple[list[str], Any]:
        self.planned.append(repo)

        async def start() -> dict[str, Any]:
            self.started += 1
            return {"state": "running", "id": "scan-1"}

        return self.reasons, start


def _tick(config: Any, rec: Recorder, *, slot: bool = True, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    async def coverage() -> list[dict[str, Any]]:
        return rows if rows is not None else schedule.coverage_rows(config, list(CONTRACTS), CONTRACTS, {}, NOW)

    return asyncio.run(schedule.tick(config=config, slot_free=lambda: slot, coverage=coverage, plan=rec.plan))


def test_tick_does_nothing_while_disabled_by_default() -> None:
    rec = Recorder()
    config = parse_operator_config({"repos": {"Tools": {"enabled": True}}})
    assert config.schedule.enabled is False
    assert _tick(config, rec) == {"started": False, "reason": "schedule disabled"}
    assert rec.planned == [] and rec.started == 0


def test_tick_respects_the_slot_and_an_empty_queue() -> None:
    rec = Recorder()
    assert _tick(_config(), rec, slot=False)["reason"] == "scan slot busy"
    assert _tick(_config(), rec, rows=[])["reason"] == "nothing due"
    assert rec.planned == []


def test_tick_never_starts_when_admission_objects() -> None:
    rec = Recorder(reasons=["outside the night window"])
    result = _tick(_config(), rec)
    assert result == {"started": False, "repo": "UpstreamDrift", "reasons": ["outside the night window"]}
    assert rec.started == 0


def test_tick_starts_the_most_urgent_due_repo() -> None:
    rec = Recorder()
    result = _tick(_config(), rec)
    assert result["started"] is True and result["repo"] == "UpstreamDrift"
    assert rec.planned == ["UpstreamDrift"] and rec.started == 1


def test_loop_survives_a_failing_tick_and_rereads_the_config() -> None:
    calls = {"tick": 0, "load": 0}
    sleeps: list[float] = []

    async def bad_tick() -> dict[str, Any]:
        calls["tick"] += 1
        raise RuntimeError("boom")

    def load() -> Any:
        calls["load"] += 1
        return _config(schedule={"enabled": True, "check_minutes": 5 if calls["load"] == 1 else 10})

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(schedule.schedule_loop(bad_tick, load=load, sleep=sleep))
    assert calls["tick"] == 2 and sleeps == [300, 600]


def test_schedule_config_validation() -> None:
    bad = parse_operator_config({"schedule": {"enabled": "yes", "check_minutes": 1}})
    assert bad.schedule.enabled is False and bad.schedule.check_minutes == 30
    assert len([e for e in bad.errors if e.startswith("schedule.")]) == 2


def test_scheduled_tick_is_inert_off_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard_config
    from vuln_scan import scan_service

    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "node")
    assert asyncio.run(scan_service.scheduled_tick()) == {"started": False, "reason": "not the hub"}


def test_latest_by_repo_ignores_dry_runs(tmp_path: Path) -> None:
    log = ScanLog(tmp_path / "db")
    kw = {"sha": SHA, "host": "scanvm", "reasons": [], "requested_by": "t"}
    log.create(repo="Tools", dry_run=True, state="planned", **kw)
    ok = log.create(repo="Tools", dry_run=False, state="running", **kw)
    log.update(ok, state="succeeded", finished="2026-10-23T09:00:00Z")
    log.create(repo="Tools", dry_run=False, state="refused", **kw)
    latest = log.latest_by_repo()
    assert latest["Tools"]["last_state"] == "refused"
    assert latest["Tools"]["last_success_at"] == "2026-10-23T09:00:00Z"
    assert list(latest) == ["Tools"]
