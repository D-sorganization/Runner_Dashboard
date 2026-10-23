"""CI helpers for the Docker image build workflow."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

ROOT = Path(__file__).resolve().parent.parent
RECOVER = ROOT / "scripts" / "ci" / "docker_buildx_recover.sh"


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_unscoped_recovery_leaves_every_builder_untouched(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    log.touch()

    docker = bin_dir / "docker"
    docker.write_text(
        f"""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "{as_bash_path(log)}"
case "$1" in
  buildx)
    case "$2" in
      version) exit 0 ;;
      ls) printf 'builder-deadbeef\\nud-buildkit\\ngm-ci-9\\n' ;;
      rm) exit 0 ;;
      prune) exit 0 ;;
    esac
    ;;
esac
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)

    result = subprocess.run(
        [BASH, as_bash_path(RECOVER)],
        env={
            "PATH": f"{as_bash_path(bin_dir)}:{as_bash_path(Path(BASH).parent)}",
            "DOCKER_HOST": "unix:///nonexistent/buildx-test.sock",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == []
