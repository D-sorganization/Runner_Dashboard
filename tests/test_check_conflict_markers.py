"""CLI for scripts.check_conflict_markers (#3408).

Extracted from tests/test_no_merge_conflict_markers.py (the required pytest
gate, #2961) so the marker regex and scan roots exist once and are reusable
from the pre-commit hook and scripts/pre_pr.py, not re-implemented per
consumer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_conflict_markers as ccm


def test_main_reports_clean_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ccm, "tracked_files", lambda root, roots=ccm.SOURCE_ROOTS: [])
    assert ccm.main(["--repository-root", str(tmp_path)]) == 0
    assert "none found" in capsys.readouterr().out


def test_main_fails_and_reports_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = tmp_path / "x.py"
    sample.write_text("a\n" + "<" * 7 + " HEAD\n", encoding="utf-8")
    monkeypatch.setattr(ccm, "tracked_files", lambda root, roots=ccm.SOURCE_ROOTS: [sample])
    assert ccm.main(["--repository-root", str(tmp_path)]) == 1
    assert "x.py:2" in capsys.readouterr().err
