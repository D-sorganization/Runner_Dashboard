"""CI helpers for the Docker image build workflow."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

ROOT = Path(__file__).resolve().parent.parent
RECOVER = ROOT / "scripts" / "ci" / "docker_buildx_recover.sh"


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_docker_buildx_recover_removes_only_ephemeral_builder_names(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"

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
        [BASH, str(RECOVER)],
        env={"PATH": f"{as_bash_path(bin_dir)}:{as_bash_path(Path(BASH).parent)}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(call == "buildx rm -f builder-deadbeef" for call in calls)
    assert any(call == "buildx rm -f gm-ci-9" for call in calls)
    assert all("ud-buildkit" not in call for call in calls if call.startswith("buildx rm"))
