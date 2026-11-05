"""Pre-push 500-line size gate, counting generated files + lockfiles (#3963 S1)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.pr_size_gate import DEFAULT_LIMIT, SizeVerdict, evaluate_size, parse_numstat

pytestmark = pytest.mark.unit


def _runner(stdout: str) -> object:
    def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return run


def test_parse_numstat_sums_added_and_deleted() -> None:
    numstat = "10\t5\tbackend/foo.py\n3\t0\tfrontend/package-lock.json\n"
    assert parse_numstat(numstat) == 18


def test_parse_numstat_skips_binary_marker_lines() -> None:
    # git numstat marks binary files with '-' for both counts.
    numstat = "10\t5\tbackend/foo.py\n-\t-\tdocs/logo.png\n"
    assert parse_numstat(numstat) == 15


def test_parse_numstat_empty_diff_is_zero() -> None:
    assert parse_numstat("") == 0


def test_evaluate_size_499_lines_passes(tmp_path: Path) -> None:
    numstat = "499\t0\tbackend/big_module.py\n"
    verdict = evaluate_size(
        tmp_path, merge_base="origin/main", limit=DEFAULT_LIMIT, pr_number=None,
        allowlist_path=tmp_path / "missing.json", run=_runner(numstat),
    )
    assert verdict == SizeVerdict(within_limit=True, total_lines=499, reason="499 <= 500")


def test_evaluate_size_501_lines_fails(tmp_path: Path) -> None:
    numstat = "501\t0\tbackend/big_module.py\n"
    verdict = evaluate_size(
        tmp_path, merge_base="origin/main", limit=DEFAULT_LIMIT, pr_number=None,
        allowlist_path=tmp_path / "missing.json", run=_runner(numstat),
    )
    assert verdict.within_limit is False
    assert verdict.total_lines == 501


def test_evaluate_size_counts_lockfile_and_generated_lines(tmp_path: Path) -> None:
    # package-lock.json and an openapi-generated file both count toward the total.
    numstat = (
        "10\t0\tbackend/foo.py\n"
        "400\t0\tfrontend/package-lock.json\n"
        "95\t0\tfrontend/src/lib/openapi.json\n"
    )
    verdict = evaluate_size(
        tmp_path, merge_base="origin/main", limit=DEFAULT_LIMIT, pr_number=None,
        allowlist_path=tmp_path / "missing.json", run=_runner(numstat),
    )
    assert verdict.total_lines == 505
    assert verdict.within_limit is False


def test_evaluate_size_allowlisted_pr_passes_despite_being_over(tmp_path: Path) -> None:
    allowlist_path = tmp_path / "pr_size_allowlist.json"
    allowlist_path.write_text(json.dumps({"allowed_prs": [2541]}), encoding="utf-8")
    numstat = "600\t0\tbackend/big_module.py\n"
    verdict = evaluate_size(
        tmp_path, merge_base="origin/main", limit=DEFAULT_LIMIT, pr_number=2541,
        allowlist_path=allowlist_path, run=_runner(numstat),
    )
    assert verdict.within_limit is True
    assert verdict.total_lines == 600
    assert "allowlisted" in verdict.reason


def test_evaluate_size_allowlist_does_not_cover_other_prs(tmp_path: Path) -> None:
    allowlist_path = tmp_path / "pr_size_allowlist.json"
    allowlist_path.write_text(json.dumps({"allowed_prs": [2541]}), encoding="utf-8")
    numstat = "600\t0\tbackend/big_module.py\n"
    verdict = evaluate_size(
        tmp_path, merge_base="origin/main", limit=DEFAULT_LIMIT, pr_number=9999,
        allowlist_path=allowlist_path, run=_runner(numstat),
    )
    assert verdict.within_limit is False


def test_evaluate_size_missing_allowlist_file_is_not_an_error(tmp_path: Path) -> None:
    numstat = "10\t0\tbackend/foo.py\n"
    verdict = evaluate_size(
        tmp_path, merge_base="origin/main", limit=DEFAULT_LIMIT, pr_number=1,
        allowlist_path=tmp_path / "does_not_exist.json", run=_runner(numstat),
    )
    assert verdict.within_limit is True


def test_main_exits_zero_when_within_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.pr_size_gate import main

    real_repo = Path(__file__).resolve().parents[2]
    exit_code = main(["--repo-root", str(real_repo), "--merge-base", "HEAD", "--limit", "500"])
    assert exit_code == 0
    assert "0 <= 500" in capsys.readouterr().out


def test_real_allowlist_file_round_trips_through_load_allowlist() -> None:
    # Regression for #3963 S3: config/pr_size_allowlist.json must parse as the
    # exact shape `_load_allowlist` expects ({"allowed_prs": [...]}) rather
    # than some other guessed shape. Starts empty; Dieter is the only editor.
    from scripts.pr_size_gate import _load_allowlist

    real_repo = Path(__file__).resolve().parents[2]
    allowlist_path = real_repo / "config" / "pr_size_allowlist.json"
    assert allowlist_path.exists()
    assert _load_allowlist(allowlist_path) == frozenset()


def test_main_exits_one_when_over_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import pr_size_gate

    def fake_run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="900\t0\tbig.py\n", stderr="")

    monkeypatch.setattr(pr_size_gate, "_run_git", fake_run)
    exit_code = pr_size_gate.main(
        ["--repo-root", str(tmp_path), "--merge-base", "origin/main", "--limit", "500"]
    )
    assert exit_code == 1
