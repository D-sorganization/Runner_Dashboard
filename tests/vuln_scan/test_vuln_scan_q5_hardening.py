"""Panel q5 hardening (#2603): patch guard, advisory text, slot liveness, build filter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from vuln_scan import advisories, scan_service, scans
from vuln_scan.operator_config import ScanHost
from vuln_scan.patches import patch_paths, protected_paths
from vuln_scan.sandbox import ExecResult
from vuln_scan.scans import ScanLog

SHA = "0bb24108aa" * 4
NL = "\n"

# ── protected-patch guard ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "patch",
    [
        # git quotes paths with spaces or special characters
        'diff --git "a/.github/workflows/c i.yml" "b/.github/workflows/c i.yml"',
        '+++ "b/.github/workflows/ci.yml"',
        # octal escapes: \056 is "."
        '+++ "b/\\056github/workflows/ci.yml"',
        # renames and copies name the target on their own lines
        "rename from src/ci.yml" + NL + "rename to .github/workflows/ci.yml",
        "copy to .oss-scanner/Dockerfile",
        # timestamps after a tab
        "--- a/.github/x.yml\t2026-10-23 10:00:00",
    ],
)
def test_quoted_renamed_and_odd_headers_are_still_protected(patch: str) -> None:
    assert protected_paths(patch), patch


def test_quoted_paths_are_unquoted() -> None:
    patch = 'diff --git "a/src/a b.py" "b/src/a b.py"' + NL + '+++ "b/src/a b.py"'
    assert patch_paths(patch) == ["src/a b.py"]
    assert protected_paths(patch) == []


def test_an_unparseable_header_mentioning_a_protected_dir_fails_closed() -> None:
    weird = '+++ b/src/x.py "and .github/workflows/ci.yml'
    assert any(p.startswith("<unparsed header>") for p in protected_paths(weird))


def test_nested_or_lookalike_dirs_are_not_protected() -> None:
    assert protected_paths("+++ b/docs/.github-notes.md") == []
    assert protected_paths("+++ b/vendor/x/.github/workflows/ci.yml") == []


# ── advisory text ───────────────────────────────────────────────────────

ROW: dict[str, Any] = {
    "title": "t",
    "cwe": "CWE-78",
    "severity": "high",
    "path": "src/export.py",
    "symbol": "x`) ## Injected" + NL + "[link](https://evil.example)",
    "root_cause": "r",
    "scan_sha": SHA,
}


def test_symbol_cannot_break_out_of_its_inline_span() -> None:
    body = advisories.advisory_body(ROW, "Tools")["description"]
    location = next(line for line in body.splitlines() if line.startswith("**Location:**"))
    assert "## Injected" in location  # kept, but inside the span on one line
    assert "``" in location  # a longer fence than the backtick in the symbol
    assert not any(line.startswith("## Injected") for line in body.splitlines())
    assert not any(line.startswith("[link]") for line in body.splitlines())


# ── slot liveness ───────────────────────────────────────────────────────


def _running(log: ScanLog) -> str:
    kw = {"sha": SHA, "host": "scanvm", "reasons": [], "requested_by": "t"}
    return log.create(repo="Tools", dry_run=False, state="running", **kw)


def test_a_beating_scan_keeps_the_slot(tmp_path: Path) -> None:
    log = ScanLog(tmp_path / "db")
    scan_id = _running(log)
    log.beat(scan_id)
    assert not log.slot_free()
    assert log.get(scan_id)["state"] == "running"


def test_a_scan_that_stopped_beating_is_reclaimed(tmp_path: Path) -> None:
    log = ScanLog(tmp_path / "db")
    scan_id = _running(log)
    with sqlite3.connect(log.path) as conn:
        conn.execute("UPDATE vs_scans SET heartbeat='2026-01-01T00:00:00Z' WHERE id=?", (scan_id,))
    assert log.slot_free()
    row = log.get(scan_id)
    assert row["state"] == "aborted" and "reclaimed" in row["error"]


def test_the_stale_horizon_is_minutes_not_hours() -> None:
    assert scans.HEARTBEAT_S * 3 < scans.STALE_HEARTBEAT_MINUTES * 60 <= 15 * 60


# ── build filter probe ──────────────────────────────────────────────────

HOST = ScanHost("scanvm", "vm", "ssh://scan@scanvm", "vs-build")


def _runner(marker: ExecResult, live: ExecResult) -> Any:
    calls: list[list[str]] = []

    def run(argv: list[str], timeout: float, cap: int, env: dict[str, str]) -> ExecResult:
        calls.append(list(argv))
        return marker if argv[0] == "ssh" else live

    run.calls = calls  # type: ignore[attr-defined]
    return run


@pytest.mark.parametrize(
    ("marker", "live", "ok"),
    [
        (ExecResult(0, "abc123\n", ""), ExecResult(0, "abc123\n", ""), True),
        (ExecResult(1, "", "No such file"), ExecResult(0, "abc123\n", ""), False),  # rebooted
        (ExecResult(0, "abc123\n", ""), ExecResult(0, "def456\n", ""), False),  # network recreated
        (ExecResult(0, "\n", ""), ExecResult(0, "\n", ""), False),
        (ExecResult(0, "abc123\n", ""), ExecResult(1, "", "boom"), False),
    ],
)
def test_build_filter_needs_a_marker_matching_the_live_network(
    monkeypatch: pytest.MonkeyPatch, marker: ExecResult, live: ExecResult, ok: bool
) -> None:
    run = _runner(marker, live)
    monkeypatch.setattr(scan_service, "subprocess_runner", run)
    assert scan_service.build_filter_ok(HOST) is ok
    assert run.calls[0][-2:] == ["cat", "/run/vs-build/vs-build.ok"]


def test_build_filter_refuses_odd_network_names_without_running_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _runner(ExecResult(0, "x", ""), ExecResult(0, "x", ""))
    monkeypatch.setattr(scan_service, "subprocess_runner", run)
    assert scan_service.build_filter_ok(ScanHost("scanvm", "vm", "ssh://scan@scanvm", "../etc")) is False
    assert run.calls == []
