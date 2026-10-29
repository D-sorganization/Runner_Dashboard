"""Panel seat turns must not abstain when ``shared_scripts`` is unimportable (#3477).

The deployed hub artifact only ships ``backend/``; ``shared_scripts`` (a repo-root
directory) does not exist there. A seat turn that imports it at call time crashes
with ``ModuleNotFoundError`` and the whole seat abstains with no synthesis.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from staff import panel_scratch

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_prepare_panel_turn_scratch_has_no_shared_scripts_import() -> None:
    """The module must never import from the repo-root ``shared_scripts`` package."""
    source = (BACKEND / "staff" / "panel_scratch.py").read_text(encoding="utf-8")
    assert "import shared_scripts" not in source
    assert "from shared_scripts" not in source


def test_prepare_panel_turn_scratch_works_without_shared_scripts_on_sys_path(tmp_path: Path) -> None:
    """Regression for #3477: calling the real function must not raise ModuleNotFoundError."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    script_path = tmp_path / "run_prepare.py"
    script_path.write_text(
        "import builtins\n"
        "import sys\n"
        f"sys.path.insert(0, {str(BACKEND)!r})\n"
        "sys.modules.pop('shared_scripts', None)\n"
        "_orig_import = builtins.__import__\n"
        "\n"
        "\n"
        "def _blocked(name, *a, **kw):\n"
        "    if name == 'shared_scripts' or name.startswith('shared_scripts.'):\n"
        "        raise ModuleNotFoundError(f'No module named {name!r}')\n"
        "    return _orig_import(name, *a, **kw)\n"
        "\n"
        "\n"
        "builtins.__import__ = _blocked\n"
        "from staff.panel_scratch import prepare_panel_turn_scratch, remove_panel_turn_scratch\n"
        "path = prepare_panel_turn_scratch()\n"
        "assert path\n"
        "remove_panel_turn_scratch(path)\n"
        "print('OK')\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-I", str(script_path)],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_prepare_panel_turn_scratch_survives_missing_fleet_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seat turn must still run when the fleet-root marker can't be written (#3477)."""
    monkeypatch.setattr(panel_scratch, "discover_fleet_repo_root", lambda _start=None: None)
    path = panel_scratch.prepare_panel_turn_scratch()
    try:
        assert Path(path).is_dir()
        assert not (Path(path) / panel_scratch.PANEL_SCRATCH_MARKER).exists()
    finally:
        panel_scratch.remove_panel_turn_scratch(path)
        shutil.rmtree(path, ignore_errors=True)


def test_prepare_panel_turn_scratch_survives_marker_write_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write failure on the marker file must be logged, not fatal (#3477)."""

    def _boom(_scratch: Path, _root: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(panel_scratch, "write_panel_scratch_marker", _boom)
    path = panel_scratch.prepare_panel_turn_scratch()
    try:
        assert Path(path).is_dir()
        assert (Path(path) / ".git").is_dir()
    finally:
        panel_scratch.remove_panel_turn_scratch(path)
        shutil.rmtree(path, ignore_errors=True)


def test_write_panel_scratch_marker_still_matches_shared_scripts_contract(
    tmp_path: Path,
) -> None:
    """The ported marker writer keeps the same on-disk contract as the RM original (#3477)."""
    fleet_root = panel_scratch.discover_fleet_repo_root(BACKEND)
    assert fleet_root is not None
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    panel_scratch.write_panel_scratch_marker(scratch, fleet_root)
    marker = scratch / panel_scratch.PANEL_SCRATCH_MARKER
    assert marker.read_text(encoding="utf-8").strip() == str(fleet_root.resolve())
