"""Tests for scripts/ci/check_junit_executed.py (issue #3776)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci" / "check_junit_executed.py"
_spec = importlib.util.spec_from_file_location("check_junit_executed", _SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


def _write(tmp_path: Path, cases: str) -> Path:
    p = tmp_path / "junit.xml"
    p.write_text(f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>')
    return p


PASS = '<testcase classname="tests.a" name="test_ok"/>'
SKIP = '<testcase classname="tests.a" name="test_skip"><skipped message="x"/></testcase>'
FAIL = '<testcase classname="tests.a" name="test_bad"><failure message="boom"/></testcase>'


def test_missing_report_fails(tmp_path: Path) -> None:
    code, msg = mod.check(tmp_path / "absent.xml")
    assert code == 2 and "not found" in msg


def test_unparseable_report_fails(tmp_path: Path) -> None:
    p = tmp_path / "junit.xml"
    p.write_text("<testsuites><testsuite")
    code, msg = mod.check(p)
    assert code == 2 and "unparseable" in msg


def test_zero_selected_fails(tmp_path: Path) -> None:
    code, msg = mod.check(_write(tmp_path, ""))
    assert code == 1 and "No test cases" in msg


def test_all_skipped_fails(tmp_path: Path) -> None:
    code, msg = mod.check(_write(tmp_path, SKIP + SKIP))
    assert code == 1 and "Only 0 of 2" in msg


def test_executed_cases_pass_and_are_listed(tmp_path: Path) -> None:
    code, msg = mod.check(_write(tmp_path, PASS + SKIP))
    assert code == 0
    assert "- tests.a::test_ok" in msg and "test_skip" not in msg.split(":", 1)[1].split("\n", 1)[1]


def test_failed_cases_count_as_executed(tmp_path: Path) -> None:
    # Pass/fail is pytest's job; this guard only proves cases ran.
    code, msg = mod.check(_write(tmp_path, FAIL))
    assert code == 0 and "tests.a::test_bad" in msg


def test_min_executed_threshold(tmp_path: Path) -> None:
    code, _ = mod.check(_write(tmp_path, PASS), min_executed=2)
    assert code == 1


def test_main_writes_step_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert mod.main([str(_write(tmp_path, PASS))]) == 0
    assert "tests.a::test_ok" in summary.read_text()
