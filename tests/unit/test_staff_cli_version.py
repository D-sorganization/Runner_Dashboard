"""Unit tests for the provider CLI minimum-version floor (#1680).

The claude floor (2.1.259) was bisected against the published npm releases:
2.1.258 rejects ``--permission-prompts none`` with ``unknown option``; 2.1.259
accepts the whole unattended argv. These tests never run a real provider CLI:
the probe is exercised against a fake executable and everything above it
against a monkeypatched probe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from staff import cli_version
from staff.adapters import ADAPTERS
from staff.classifier import ALLOWED_FAILURE_CLASSES
from staff.retry import NON_RETRYABLE_FAILURE_CLASSES, is_retryable_class
from staff.runner_ops import can_run_unattended, fail_if_cli_outdated, select_first_available_provider
from staff.store import RunRecord, RunStore


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    cli_version.reset_cache()


def _pin(monkeypatch: pytest.MonkeyPatch, version: str | None, *, installed: bool = True) -> list[str]:
    """Make ``claude`` resolve to a fixed path whose probe reports ``version``; returns the probe log."""
    calls: list[str] = []
    monkeypatch.setattr(cli_version.shutil, "which", lambda exe: f"/opt/{exe}" if installed else None)
    monkeypatch.setattr(cli_version, "_binary_key", lambda path: (path, 1))

    def fake_probe(path: str) -> tuple[int, int, int] | None:
        calls.append(path)
        return cli_version.parse_version(version or "")

    monkeypatch.setattr(cli_version, "_probe", fake_probe)
    return calls


def _rec(run_id: str, status: str) -> RunRecord:
    return RunRecord(
        id=run_id,
        role="barb",
        provider="claude",
        model=None,
        machine="DeskComputer",
        repo="UpstreamDrift",
        target_kind="issue",
        target_ref="7",
        prompt="status",
        status=status,
    )


# ── parsing and comparison ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2.1.280 (Claude Code)", (2, 1, 280)),
        ("2.1.79 (Claude Code)\n", (2, 1, 79)),
        ("claude 10.0.3", (10, 0, 3)),
        ("v2.1.259-beta.1", (2, 1, 259)),
        ("", None),
        ("Claude Code", None),
        ("2.1", None),
    ],
)
def test_parse_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert cli_version.parse_version(text) == expected


@pytest.mark.unit
def test_versions_compare_numerically_not_lexically() -> None:
    # "2.1.79" > "2.1.259" as strings; the floor must compare as integers.
    assert cli_version.parse_version("2.1.79") < cli_version.parse_version("2.1.259")  # type: ignore[operator]
    assert cli_version.format_version((2, 1, 259)) == "2.1.259"


@pytest.mark.unit
def test_claude_floor_is_the_verified_minimum() -> None:
    assert cli_version.MIN_CLI_VERSIONS["claude"] == (2, 1, 259)
    # claude and claude-ollama share the executable, so they share the floor.
    assert ADAPTERS["claude"].executable == ADAPTERS["claude-ollama"].executable == "claude"


# ── the probe against a fake executable ───────────────────────────────────


def _fake_cli(tmp_path: Path, output: str, exit_code: int = 0) -> str:
    script = tmp_path / "fake_cli.py"
    script.write_text(f"import sys\nsys.stdout.write({output!r})\nsys.exit({exit_code})\n", encoding="utf-8")
    if os.name == "nt":
        launcher = tmp_path / "fake_cli.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "fake_cli"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return str(launcher)


@pytest.mark.unit
def test_probe_reads_version_from_fake_executable(tmp_path: Path) -> None:
    assert cli_version._probe(_fake_cli(tmp_path, "2.1.283 (Claude Code)\n")) == (2, 1, 283)


@pytest.mark.unit
def test_probe_returns_none_on_failure_or_garbage(tmp_path: Path) -> None:
    assert cli_version._probe(_fake_cli(tmp_path, "2.1.283\n", exit_code=1)) is None
    assert cli_version._probe(_fake_cli(tmp_path / ".." / tmp_path.name, "not a version\n")) is None
    assert cli_version._probe(str(tmp_path / "does-not-exist")) is None


# ── cache ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_version_is_cached_per_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _pin(monkeypatch, "2.1.280 (Claude Code)")
    assert cli_version.installed_version("claude") == (2, 1, 280)
    assert cli_version.installed_version("claude") == (2, 1, 280)
    assert calls == ["/opt/claude"]


@pytest.mark.unit
def test_cache_reprobes_when_the_binary_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _pin(monkeypatch, "2.1.79 (Claude Code)")
    assert cli_version.installed_version("claude") == (2, 1, 79)
    # An in-place upgrade changes the resolved binary's identity (symlink target or mtime).
    monkeypatch.setattr(cli_version, "_binary_key", lambda path: (path, 2))
    monkeypatch.setattr(cli_version, "_probe", lambda path: calls.append(path) or (2, 1, 283))
    assert cli_version.installed_version("claude") == (2, 1, 283)
    assert len(calls) == 2


# ── status and gate ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_status_below_floor_is_outdated_with_remediation(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch, "2.1.79 (Claude Code)")
    status = cli_version.cli_status("claude")
    assert status.installed and status.outdated
    assert status.version == "2.1.79" and status.min_version == "2.1.259"
    assert "claude CLI 2.1.79 < required 2.1.259; upgrade the CLI on this node" in status.detail


@pytest.mark.unit
@pytest.mark.parametrize("version", ["2.1.259", "2.1.280", "3.0.0"])
def test_status_at_or_above_floor_is_supported(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    _pin(monkeypatch, version)
    status = cli_version.cli_status("claude")
    assert status.installed and not status.outdated and status.version == version
    assert cli_version.version_gate("claude") is None


@pytest.mark.unit
def test_unknown_version_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch, None)
    status = cli_version.cli_status("claude")
    assert status.installed and not status.outdated and status.version is None
    assert "could not be read" in status.detail
    assert cli_version.version_gate("claude") is None


@pytest.mark.unit
def test_missing_or_unfloored_executable_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _pin(monkeypatch, "0.0.1", installed=False)
    assert cli_version.cli_status("claude").installed is False
    assert cli_version.version_gate("claude") is None
    assert cli_version.version_gate("codex") is None
    assert cli_version.version_gate("") is None
    assert calls == []


@pytest.mark.unit
def test_gate_is_non_retryable_cli_outdated(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch, "2.1.79 (Claude Code)")
    gate = cli_version.version_gate("claude")
    assert gate is not None
    assert gate.failure_class == "cli_outdated" and gate.retryable is False
    assert gate.failure_class in ALLOWED_FAILURE_CLASSES
    assert gate.failure_class in NON_RETRYABLE_FAILURE_CLASSES and not is_retryable_class(gate.failure_class)
    assert gate.remediation.startswith("claude CLI 2.1.79 < required 2.1.259; upgrade the CLI on this node")
    assert "2.1.79" in gate.error


# ── provider selection and the unattended-run gate ─────────────────────────


@pytest.mark.unit
def test_can_run_unattended_is_false_for_outdated_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch, "2.1.79")
    assert can_run_unattended(ADAPTERS, "claude") is False
    assert can_run_unattended(ADAPTERS, "claude-ollama") is False
    cli_version.reset_cache()
    _pin(monkeypatch, "2.1.283")
    assert can_run_unattended(ADAPTERS, "claude") is True


@pytest.mark.unit
def test_provider_selection_skips_outdated_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch, "2.1.79")

    class Installed:
        def __init__(self, executable: str) -> None:
            self.executable = executable
            self.unattended = True

        def installed(self) -> bool:
            return True

    adapters: dict[str, Any] = {"claude": Installed("claude"), "codex": Installed("codex")}
    assert select_first_available_provider(adapters, ("claude", "codex"), ceiling=100.0) == "codex"


@pytest.mark.unit
def test_run_fails_up_front_below_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _pin(monkeypatch, "2.1.79 (Claude Code)")
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = store.create_run(_rec("r1", "preparing"))

    assert fail_if_cli_outdated(store, rec, ADAPTERS["claude"]) is True

    row = store.get_run("r1")
    assert row is not None
    assert row.status == "failed" and row.failure_class == "cli_outdated" and row.retryable is False
    assert "claude CLI 2.1.79 < required 2.1.259" in row.remediation
    assert row.ended_at


@pytest.mark.unit
def test_run_passes_gate_at_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _pin(monkeypatch, "2.1.259")
    store = RunStore(tmp_path / "runs.sqlite3")
    rec = store.create_run(_rec("r2", "preparing"))
    assert fail_if_cli_outdated(store, rec, ADAPTERS["claude"]) is False
    row = store.get_run("r2")
    assert row is not None and row.status == "preparing"


@pytest.mark.unit
def test_staff_runner_worker_stops_before_workdir_and_lease(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from staff.plan import RunRequest
    from staff.roles import RoleSpec
    from staff.runner import StaffRunner

    _pin(monkeypatch, "2.1.283")  # plan() needs a runnable provider
    role = RoleSpec(name="barb", title="Barb", providers=("claude",))
    runner = StaffRunner(store=RunStore(tmp_path / "runs.sqlite3"), roles_loader=lambda: {"barb": role})
    plan = runner.plan(RunRequest(role="barb", provider="claude", prompt="status", repo="UpstreamDrift", issue="7"))
    rec = runner.store.create_run(_rec("r3", "queued"))

    cli_version.reset_cache()
    _pin(monkeypatch, "2.1.79")
    monkeypatch.setattr(runner, "_prepare_workdir", lambda *a: pytest.fail("worktree prepared for an outdated CLI"))
    monkeypatch.setattr("staff.lease.acquire", lambda *a, **k: pytest.fail("lease taken for an outdated CLI"))
    runner._worker(rec, plan)

    row = runner.store.get_run("r3")
    assert row is not None and row.status == "failed" and row.failure_class == "cli_outdated"


# ── chat gate ──────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_turn_refused_below_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from staff.chat import ChatTurnRunner
    from staff.conversations import get_conversation_store, reset_conversation_store
    from staff.thread_bus import reset_thread_bus

    reset_conversation_store()
    reset_thread_bus()
    runner = ChatTurnRunner(conv_store=get_conversation_store(tmp_path / "conv.sqlite3"))
    _pin(monkeypatch, "2.1.79")
    monkeypatch.setattr(runner, "_spawn_cli_process", lambda **k: pytest.fail("spawned an outdated CLI"))

    result = await runner._run_turn_attempt(
        thread_id="t1",
        placeholder_id="p1",
        role=None,
        adapter=ADAPTERS["claude"],
        prompt="hello",
        session_id=None,
        is_resume=False,
    )
    assert result.ok is False
    assert result.failure_class == "cli_outdated" and result.retryable is False
    assert "claude CLI 2.1.79 < required 2.1.259" in result.remediation


@pytest.mark.unit
def test_cli_outdated_outranks_generic_chat_failures() -> None:
    from staff.chat_failures import failure_specificity

    assert failure_specificity("cli_outdated") > failure_specificity("unknown")
    assert failure_specificity("cli_outdated") > failure_specificity("cli_missing")


# ── roster payload ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_provider_versions_reports_floored_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch, "2.1.79")
    versions = cli_version.provider_versions(ADAPTERS)
    assert set(versions) == {"claude", "claude-ollama"}
    assert versions["claude"] == {
        "executable": "claude",
        "installed": True,
        "version": "2.1.79",
        "min_version": "2.1.259",
        "outdated": True,
        "detail": versions["claude"]["detail"],
    }
    assert "upgrade the CLI on this node" in versions["claude"]["detail"]


@pytest.mark.unit
def test_roster_models_accept_provider_versions() -> None:
    from staff.models import StaffRosterResponse

    body = StaffRosterResponse(
        machine="DeskComputer",
        roles=[],
        providers={"claude": True},
        provider_versions={
            "claude": {
                "executable": "claude",
                "installed": True,
                "version": "2.1.79",
                "min_version": "2.1.259",
                "outdated": True,
                "detail": "claude CLI 2.1.79 < required 2.1.259; upgrade the CLI on this node",
            }
        },
    )
    assert body.provider_versions["claude"].outdated is True
    assert StaffRosterResponse(machine="m", roles=[], providers={}).provider_versions == {}


@pytest.mark.unit
def test_floor_failure_reuses_the_classifier_upgrade_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff.classifier import UPGRADE_COMMANDS, classify_cli_below_floor

    _pin(monkeypatch, "2.1.79")
    gate = cli_version.version_gate("claude")
    assert gate == classify_cli_below_floor("claude", "2.1.79", "2.1.259")
    assert UPGRADE_COMMANDS["claude"] in gate.remediation
    assert cli_version.cli_status("claude").detail == gate.remediation
