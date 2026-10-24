"""Tests for scripts/agent-check-regen.py (pre-push API contract regeneration)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "agent-check-regen.py"


def test_regen_skips_when_diff_does_not_touch_api_contract(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "regenerated" not in (proc.stdout or "").lower()


def test_regen_hook_script_exists() -> None:
    assert SCRIPT.is_file()
