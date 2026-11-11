"""Tests for merge_group coverage combine + fail_under enforcement (#4299)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.enforce_coverage_floor import discover_data_files, enforce, load_fail_under


def test_discover_data_files_finds_dot_coverage_variants(tmp_path: Path) -> None:
    (tmp_path / ".coverage").write_text("x", encoding="utf-8")
    (tmp_path / ".coverage.host.12345").write_text("y", encoding="utf-8")
    (tmp_path / "coverage.xml").write_text("<xml/>", encoding="utf-8")
    found = {p.name for p in discover_data_files(tmp_path)}
    assert found == {".coverage", ".coverage.host.12345"}


def test_load_fail_under_reads_pyproject(repo_root: Path) -> None:
    assert load_fail_under(repo_root / "pyproject.toml") >= 60


def test_enforce_combines_parallel_shards_via_directory_scan(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shard artifacts can include a stale ``.coverage`` plus ``.coverage.*`` siblings."""
    coverage = pytest.importorskip("coverage")
    monkeypatch.chdir(tmp_path)
    module = Path("backend/covered.py")
    module.parent.mkdir(parents=True)
    module.write_text("a = 1\nb = 2\n", encoding="utf-8")
    lines = {str(module.resolve()): [1, 2]}

    for suffix in ("", ".shard1", ".shard2"):
        data = coverage.CoverageData(basename=str(tmp_path / f".coverage{suffix}"))
        data.add_lines(lines)
        data.write()

    assert len(discover_data_files(tmp_path)) >= 3

    seen_paths: list[list[str]] = []
    original_combine = coverage.Coverage.combine

    def recording_combine(self, data_paths=None, strict=False, keep=False):
        if data_paths is not None:
            seen_paths.append(list(data_paths))
        return original_combine(self, data_paths=data_paths, strict=strict, keep=keep)

    monkeypatch.setattr(coverage.Coverage, "combine", recording_combine)

    code, message = enforce(tmp_path, repo_root / "pyproject.toml")
    assert code == 0
    assert "meets fail_under" in message
    assert seen_paths == [[str(tmp_path.resolve())]]


def test_enforce_passes_when_combined_total_meets_floor(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coverage = pytest.importorskip("coverage")
    monkeypatch.chdir(tmp_path)
    module = Path("backend/covered.py")
    module.parent.mkdir(parents=True)
    module.write_text("a = 1\nb = 2\n", encoding="utf-8")

    for suffix in ("", ".shard1", ".shard2"):
        data = coverage.CoverageData(basename=str(tmp_path / f".coverage{suffix}"))
        data.add_lines({str(module.resolve()): [1, 2]})
        data.write()

    code, message = enforce(tmp_path, repo_root / "pyproject.toml")
    assert code == 0
    assert "meets fail_under" in message


def test_enforce_fails_when_total_below_floor(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coverage = pytest.importorskip("coverage")
    monkeypatch.chdir(tmp_path)
    covered = Path("backend/hit.py")
    covered.parent.mkdir(parents=True)
    covered.write_text("x = 1\n", encoding="utf-8")
    missed = Path("backend/missed.py")
    missed.write_text("".join(f"v{i}={i}\n" for i in range(50)), encoding="utf-8")

    data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
    data.add_lines({str(covered.resolve()): [1]})
    data.touch_file(str(missed.resolve()))
    data.write()

    code, message = enforce(tmp_path, repo_root / "pyproject.toml")
    assert code == 1
    assert "below fail_under" in message


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
