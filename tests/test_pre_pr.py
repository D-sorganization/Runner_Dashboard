"""scripts/pre_pr.py orchestrates RD's local merge-gate mirror (#3408).

AGENTS.md's synced PR Lifecycle rule says to run `python -m scripts.pre_pr`
before pushing, but this repo had neither the module nor a shared
implementation, so the command failed outright with `No module named
scripts.pre_pr`.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from scripts import pre_pr


def test_main_passes_when_both_gates_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pre_pr.verify_merge_gates, "main", lambda argv=None: 0)
    monkeypatch.setattr(pre_pr.check_conflict_markers, "main", lambda argv=None: 0)
    assert pre_pr.main([]) == 0


def test_main_fails_when_merge_gates_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pre_pr.verify_merge_gates, "main", lambda argv=None: 1)
    monkeypatch.setattr(pre_pr.check_conflict_markers, "main", lambda argv=None: 0)
    assert pre_pr.main([]) == 1


def test_main_fails_when_conflict_markers_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pre_pr.verify_merge_gates, "main", lambda argv=None: 0)
    monkeypatch.setattr(pre_pr.check_conflict_markers, "main", lambda argv=None: 1)
    assert pre_pr.main([]) == 1


def test_main_forwards_repository_root_and_base_to_merge_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_gates: dict[str, Sequence[str] | None] = {}
    captured_markers: dict[str, Sequence[str] | None] = {}

    def fake_gates(argv: Sequence[str] | None = None) -> int:
        captured_gates["argv"] = argv
        return 0

    def fake_markers(argv: Sequence[str] | None = None) -> int:
        captured_markers["argv"] = argv
        return 0

    monkeypatch.setattr(pre_pr.verify_merge_gates, "main", fake_gates)
    monkeypatch.setattr(pre_pr.check_conflict_markers, "main", fake_markers)

    pre_pr.main(["--repository-root", "/tmp/x", "--base", "origin/develop"])

    assert captured_gates["argv"] == ["--repository-root", "/tmp/x", "--base", "origin/develop"]
    assert captured_markers["argv"] == ["--repository-root", "/tmp/x"]
