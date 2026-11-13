"""Tests for scripts/agent-check-regen.py (pre-push API contract regeneration)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "agent-check-regen.py"


def test_regen_skips_when_diff_does_not_touch_api_contract(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env={
            **os.environ,
            "FLEET_HOOK_FROM_REF": "HEAD",
            "FLEET_HOOK_TO_REF": "HEAD",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "regenerated" not in (proc.stdout or "").lower()


def test_regen_hook_script_exists() -> None:
    assert SCRIPT.is_file()
