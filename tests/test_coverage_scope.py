"""The coverage floor counts this checkout's ``backend/`` only (#2914).

pytest-cov hands ``--cov=backend`` to child processes as a relative
``COV_CORE_SOURCE``. A Python child running in another directory (a git
credential helper in an operator checkout, say) resolves ``backend`` against its
own cwd and records every file there as never executed. On a host runner that
added a second copy of ``backend/`` and dropped the total from ~84% to 43%.
"""

from __future__ import annotations

import io
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"


def test_coverage_report_is_restricted_to_the_checkouts_backend() -> None:
    report = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["coverage"]["report"]
    assert report["include"] == ["backend/*"]
    assert report["fail_under"] >= 60


def test_foreign_backend_tree_does_not_count_toward_the_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coverage = pytest.importorskip("coverage")
    workspace = tmp_path / "workspace"
    foreign = tmp_path / "operator-checkout"
    (workspace / "backend").mkdir(parents=True)
    (foreign / "backend").mkdir(parents=True)
    own = workspace / "backend" / "own.py"
    own.write_text("a = 1\nb = 2\n", encoding="utf-8")
    other = foreign / "backend" / "other.py"
    other.write_text("".join(f"x{i} = {i}\n" for i in range(20)), encoding="utf-8")

    data_file = tmp_path / ".coverage"
    data = coverage.CoverageData(basename=str(data_file))
    data.add_lines({str(own): [1, 2]})
    data.touch_file(str(other))  # what a child process in the foreign cwd records
    data.write()

    monkeypatch.chdir(workspace)
    cov = coverage.Coverage(data_file=str(data_file), config_file=str(PYPROJECT))
    cov.load()
    out = io.StringIO()
    total = cov.report(file=out)

    assert "other.py" not in out.getvalue()
    assert total == pytest.approx(100.0)
