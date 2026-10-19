"""refresh-token.sh must not re-save the stale GH_TOKEN it inherits.

systemd loads the secrets EnvironmentFile into ExecStartPre too, and
``gh auth token`` returns ``$GH_TOKEN`` when it is set. Without clearing it,
the script writes the old (possibly revoked) token back after every
``gh auth login``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from bash_host import find_bash

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "refresh-token.sh"
_STALE = "gho_" + "s" * 36
_FRESH = "gho_" + "f" * 36

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs a POSIX bash",
)


def _fake_gh(bin_dir: Path) -> None:
    gh = bin_dir / "gh"
    # Mirrors real gh: an exported GH_TOKEN wins over stored credentials.
    gh.write_text(f'#!/usr/bin/env bash\necho "${{GH_TOKEN:-{_FRESH}}}"\n', encoding="utf-8")
    gh.chmod(0o755)


def test_refresh_ignores_inherited_stale_gh_token(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_gh(bin_dir)
    secrets = tmp_path / ".config" / "runner-dashboard" / "env"
    secrets.parent.mkdir(parents=True)
    secrets.write_text(f"FLEET_NODES=x\nGH_TOKEN={_STALE}\n", encoding="utf-8")

    env = {
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GH_TOKEN": _STALE,
        "GITHUB_TOKEN": _STALE,
    }
    subprocess.run([find_bash() or "bash", str(_SCRIPT)], env=env, check=True, capture_output=True)

    content = secrets.read_text(encoding="utf-8")
    assert f"GH_TOKEN={_FRESH}" in content
    assert _STALE not in content
    assert "FLEET_NODES=x" in content
