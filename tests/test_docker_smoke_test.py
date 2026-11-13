"""CI helper: smoke-test the built Docker image before trusting the gate (#3178)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

ROOT = Path(__file__).resolve().parent.parent
SMOKE_TEST = ROOT / "scripts" / "ci" / "docker_smoke_test.sh"

_DOCKER_STUB_TEMPLATE = """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "{log}"
case "$1" in
  run)
    exit {run_exit}
    ;;
  logs)
    printf '{fake_logs}\\n'
    exit 0
    ;;
  rm)
    exit 0
    ;;
  inspect)
    exit 0
    ;;
esac
exit 0
"""

_CURL_STUB_TEMPLATE = """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "{log}"
case "$*" in
  *livez*)
    exit {livez_exit}
    ;;
  *)
    printf '{index_body}'
    exit {index_exit}
    ;;
esac
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _run_smoke_test(
    tmp_path: Path,
    *,
    run_exit: int = 0,
    livez_exit: int = 0,
    index_exit: int = 0,
    index_body: str = '<div id="root"></div>',
    fake_logs: str = "container boot log",
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    log.touch()

    _write_stub(
        bin_dir / "docker",
        _DOCKER_STUB_TEMPLATE.format(
            log=as_bash_path(log), run_exit=run_exit, fake_logs=fake_logs
        ),
    )
    _write_stub(
        bin_dir / "curl",
        _CURL_STUB_TEMPLATE.format(
            log=as_bash_path(log),
            livez_exit=livez_exit,
            index_exit=index_exit,
            index_body=index_body,
        ),
    )

    result = subprocess.run(
        [BASH, as_bash_path(SMOKE_TEST), "runner-dashboard:ci-test"],
        env={
            "PATH": f"{as_bash_path(bin_dir)}:{as_bash_path(Path(BASH).parent)}",
            "SMOKE_TEST_RETRIES": "2",
            "SMOKE_TEST_SLEEP": "0",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    result.calls = log.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    return result


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_requires_an_image_tag_argument() -> None:
    result = subprocess.run(
        [BASH, as_bash_path(SMOKE_TEST)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_passes_when_livez_healthy_and_ui_index_served(tmp_path: Path) -> None:
    result = _run_smoke_test(tmp_path)
    assert result.returncode == 0, result.stderr
    calls = result.calls  # type: ignore[attr-defined]
    assert any(c.startswith("run ") for c in calls)
    assert any("livez" in c for c in calls)
    assert any(c.startswith("rm ") for c in calls)


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_fails_and_dumps_logs_when_image_never_reports_healthy(tmp_path: Path) -> None:
    result = _run_smoke_test(tmp_path, livez_exit=1)
    assert result.returncode != 0
    assert "never reported healthy" in (result.stdout + result.stderr).lower()
    calls = result.calls  # type: ignore[attr-defined]
    assert any(c.startswith("logs ") for c in calls)
    assert any(c.startswith("rm ") for c in calls)


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_fails_when_ui_index_is_not_served(tmp_path: Path) -> None:
    result = _run_smoke_test(tmp_path, index_body="Internal Server Error", index_exit=1)
    assert result.returncode != 0
    assert "ui index" in (result.stdout + result.stderr).lower()
    calls = result.calls  # type: ignore[attr-defined]
    assert any(c.startswith("logs ") for c in calls)
    assert any(c.startswith("rm ") for c in calls)


@pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
def test_container_is_always_removed_even_on_failure(tmp_path: Path) -> None:
    result = _run_smoke_test(tmp_path, livez_exit=1)
    assert result.returncode != 0
    calls = result.calls  # type: ignore[attr-defined]
    assert any(c.startswith("rm ") for c in calls)
