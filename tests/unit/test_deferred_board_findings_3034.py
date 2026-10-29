"""Regression and verification-first tests for deferred board findings (#3034)."""

from __future__ import annotations

import asyncio
import datetime as _dt
import enum as _enum
import gzip
import sys
import typing as _typing
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typing_extensions as _te

if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017
if not hasattr(_enum, "StrEnum"):

    class _StrEnum(str, _enum.Enum):  # noqa: UP042
        pass

    _enum.StrEnum = _StrEnum  # type: ignore[attr-defined]
if not hasattr(_typing, "Self"):
    _typing.Self = _te.Self  # type: ignore[attr-defined]

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_rd_20_cordon_state_falls_back_when_default_path_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RD-20: save_cordon_state falls back to user state dir when /var/lib path is unwritable."""
    import disk_full_cordon_runtime as dfcr
    from disk_full_cordon import DiskFullCordonState

    fallback_dir = tmp_path / "state-dir"
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(fallback_dir))
    monkeypatch.delenv("DISK_FULL_CORDON_STATE_PATH", raising=False)

    unwritable = tmp_path / "unwritable" / "disk-full-cordon-state.json"
    monkeypatch.setattr(dfcr, "_STATE_PATH", unwritable)

    orig_mkdir = Path.mkdir

    def guarded_mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
        if "unwritable" in str(self):
            raise PermissionError("Permission denied: /var/lib/runner-dashboard")
        orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)

    state = DiskFullCordonState(phase="tripping", tripped_at=100.0, cleanup_done=False)
    dfcr.save_cordon_state(state)

    loaded = dfcr.load_cordon_state()
    assert loaded.phase == "tripping"
    assert loaded.tripped_at == 100.0
    assert (fallback_dir / "disk-full-cordon-state.json").is_file()


def test_rd_28_reaper_retains_principal_when_release_tracked_claims_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RD-28: transient error in release_tracked_claims must not drop_principal."""
    from agent_bus import reaper

    stale_row = MagicMock()
    stale_row.session_id = "sess-transient-err"
    stale_row.role = "issue-remediator"

    fake_store = MagicMock()
    fake_store.list_stale_principals.return_value = [stale_row]
    fake_store.release_tracked_claims.side_effect = RuntimeError("RM GitHub API 502")

    monkeypatch.setattr(reaper, "get_store", lambda: fake_store)

    result = reaper.reap_once()
    fake_store.drop_principal.assert_not_called()
    assert result["released_claims"] == 0


@pytest.mark.asyncio
async def test_rd_31_watchdog_heartbeat_survives_transient_notifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RD-31: _watchdog_heartbeat continues after a transient notifier error."""
    import server

    calls: list[str] = []

    def flaky_notify(msg: str) -> None:
        calls.append(msg)
        if len(calls) == 1:
            raise OSError("transient notify socket error")

    class _StopWatchdog(BaseException):
        pass

    sleep_count = 0

    async def fake_sleep(_interval: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise _StopWatchdog

    monkeypatch.setattr(server, "_sd_notify", flaky_notify)
    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopWatchdog):
        await server._watchdog_heartbeat()

    assert len(calls) == 2
    assert calls == ["WATCHDOG=1", "WATCHDOG=1"]


@pytest.mark.asyncio
async def test_rd_34_diagnostics_summary_reports_unknown_drift_on_probe_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """RD-34: when _get_git_drift raises, diagnostics logs warning and sets is_drifted=None."""
    from routers import diagnostics

    async def failing_git_drift() -> dict[str, Any]:
        raise RuntimeError("git rev-parse timed out")

    async def noop_vhdx() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(diagnostics, "_get_git_drift", failing_git_drift)
    monkeypatch.setattr(diagnostics, "query_wsl_vhdx_status", noop_vhdx)
    monkeypatch.setattr(
        diagnostics,
        "detect_sharing_violations",
        lambda *_a, **_k: {"detected": False, "error_code": None, "target_file": None, "message": None},
    )

    with caplog.at_level("WARNING", logger="dashboard.diagnostics"):
        summary = await diagnostics.get_diagnostics_summary()

    assert summary["is_drifted"] is None
    assert any("git drift" in rec.message.lower() for rec in caplog.records)

    md = diagnostics.generate_markdown_artifact_content(summary)
    assert "- Drifted: `unknown`" in md
    assert "- Drifted: `False`" not in md


def test_rd_35_corrupt_auto_deploy_state_sets_attention_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RD-35: corrupt auto_deploy_state.json reports attention=True instead of looking healthy."""
    from node_auto_deploy_status import read_auto_deploy_fleet_status

    missing = tmp_path / "missing_state.json"
    monkeypatch.setenv("RUNNER_DASHBOARD_AUTO_DEPLOY_STATE", str(missing))
    healthy_default = read_auto_deploy_fleet_status()
    assert healthy_default["attention"] is False
    assert healthy_default["last_result"] is None

    corrupt = tmp_path / "corrupt_state.json"
    corrupt.write_text("{corrupt-json", encoding="utf-8")
    monkeypatch.setenv("RUNNER_DASHBOARD_AUTO_DEPLOY_STATE", str(corrupt))
    corrupt_status = read_auto_deploy_fleet_status()
    assert corrupt_status["attention"] is True
    assert corrupt_status["last_result"] == "error"

    non_dict = tmp_path / "list_state.json"
    non_dict.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv("RUNNER_DASHBOARD_AUTO_DEPLOY_STATE", str(non_dict))
    non_dict_status = read_auto_deploy_fleet_status()
    assert non_dict_status["attention"] is True
    assert non_dict_status["last_result"] == "error"


@pytest.mark.asyncio
async def test_rd_36_async_subprocess_helpers_reap_after_kill_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RD-36: async subprocess timeout handlers suppress ProcessLookupError and await proc.wait()."""
    import issue_inventory
    import pr_inventory
    import server
    import workflow_stats

    def make_timed_out_proc() -> MagicMock:
        proc = MagicMock()
        proc.communicate = MagicMock(return_value=asyncio.Future())
        proc.kill = MagicMock(side_effect=ProcessLookupError("already exited"))
        proc.wait = AsyncMock(return_value=-9)
        return proc

    async def fake_wait_for(coro: Any, timeout: float) -> Any:
        if hasattr(coro, "close"):
            coro.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    for mod, fn_name, arg in [
        (issue_inventory, "_run_gh", ["issue", "list"]),
        (pr_inventory, "_run_gh", ["pr", "list"]),
        (server, "run_cmd", ["gh", "version"]),
        (workflow_stats, "_gh_api_json", "repos/o/r/actions/runs"),
    ]:
        proc = make_timed_out_proc()
        with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
            res = await getattr(mod, fn_name)(arg, timeout=1)
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()
        if fn_name == "_gh_api_json":
            assert res is None
        else:
            assert res[0] == -1


def test_rd_38_archive_old_audit_entries_verifies_archive_before_deleting_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RD-38: archive_old_audit_entries verifies gzip archive before deleting SQLite rows."""
    from staff.audit import AuditError, StaffAuditRecord, StaffAuditStore, archive_old_audit_entries

    db_file = tmp_path / "audit.sqlite3"
    archive_dir = tmp_path / "archive"
    store = StaffAuditStore(db_file)
    store.record(
        StaffAuditRecord(
            ts="2025-01-01T00:00:00Z",
            principal="agent-a",
            action="dispatch",
            surface="scheduler",
            target="role-a",
        )
    )
    assert store.count_entries() == 1

    orig_gzip_open = gzip.open

    def corrupt_read_gzip_open(filename: Any, mode: str = "rb", *args: Any, **kwargs: Any) -> Any:
        if "r" in mode:
            raise OSError("corrupt gzip stream on verification read-back")
        return orig_gzip_open(filename, mode, *args, **kwargs)

    monkeypatch.setattr(gzip, "open", corrupt_read_gzip_open)

    with pytest.raises(AuditError):
        archive_old_audit_entries(
            store=store,
            retention_days=180,
            archive_dir=archive_dir,
            reference_date="2026-09-24T12:00:00Z",
        )

    assert store.count_entries() == 1
    store.close()


def test_rd_40_request_id_middleware_is_outermost() -> None:
    """RD-40: RequestIdMiddleware is registered at index 0 (outermost) of app.user_middleware."""
    import server
    from request_context import RequestIdMiddleware

    assert server.app.user_middleware, "Expected middlewares registered on server.app"
    assert server.app.user_middleware[0].cls is RequestIdMiddleware


def test_rd_32_leased_runners_no_leases_key_asserts_empty_set(tmp_path: Path) -> None:
    """RD-32: test_leased_runners_no_leases_key executes real assertions against _leased_runners()."""
    import importlib.util
    import inspect

    mod_path = Path(__file__).resolve().parents[1] / "test_runner_autoscaler.py"
    spec = importlib.util.spec_from_file_location("tra", mod_path)
    assert spec and spec.loader
    tra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tra)

    tra.test_leased_runners_no_leases_key(tmp_path)
    src = inspect.getsource(tra.test_leased_runners_no_leases_key)
    assert "assert ra._leased_runners() == set()" in src
    assert "pass" not in src


def test_rd_39_contract_and_capacity_modules_import_and_validate(tmp_path: Path) -> None:
    """RD-39: capacity_reservation and remote_execution_contract are active contract modules."""
    import remote_execution_contract as rec
    from staff.capacity_reservation import CapacityLimits, CapacityReservationStore

    assert rec.SCHEMA_VERSION == "remote-execution-envelope.v1"
    limits = CapacityLimits(max_concurrent_slots=4, repair_reserved_slots=1)
    store = CapacityReservationStore(path=tmp_path / "cap.db", limits=limits)
    decision = store.reserve("op-3034", role="bob", provider="claude", slots=1)
    assert decision.admitted is True


def test_rd_46_agent_bus_page_clears_error_on_successful_poll() -> None:
    """RD-46: AgentBusPage clears error state when a subsequent poll succeeds."""
    page_src = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "AgentBus" / "AgentBusPage.tsx"
    ).read_text(encoding="utf-8")
    assert "setData(res)" in page_src and "setError(null)" in page_src
