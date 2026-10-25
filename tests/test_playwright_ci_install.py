"""Playwright CI install must not require sudo on self-hosted fleet runners."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml
from bash_host import require_bash

_REPO = Path(__file__).parent.parent
_SCRIPT = _REPO / "scripts" / "ci_install_playwright_browsers.sh"
_FRONTEND_TESTS = _REPO / ".github" / "workflows" / "frontend-tests.yml"


def _run_install_script(
    tmp_path: Path,
    *,
    runner_environment: str,
    deps_dry_run_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    log = tmp_path / "npx.log"
    stub = f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{log}"
case "$1" in
  playwright)
    case "$2" in
      install)
        if [[ "$*" == *"--with-deps"* ]]; then
          echo "with-deps" >> "{log}.mode"
        else
          echo "browsers-only" >> "{log}.mode"
        fi
        ;;
      install-deps)
        exit {deps_dry_run_exit}
        ;;
    esac
    ;;
esac
exit 0
"""
    npx = tmp_path / "npx"
    npx.write_text(stub, encoding="utf-8")
    npx.chmod(0o755)
    return subprocess.run(
        [require_bash(), str(_SCRIPT), "chromium"],
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
            "RUNNER_ENVIRONMENT": runner_environment,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def test_install_script_uses_with_deps_only_on_github_hosted(tmp_path: Path) -> None:
    hosted = _run_install_script(tmp_path, runner_environment="github-hosted")
    assert hosted.returncode == 0, hosted.stderr
    mode = (tmp_path / "npx.log.mode").read_text(encoding="utf-8")
    assert mode.strip() == "with-deps"


def test_install_script_on_self_hosted_skips_with_deps_and_preflights_deps(tmp_path: Path) -> None:
    fleet = _run_install_script(tmp_path, runner_environment="self-hosted", deps_dry_run_exit=0)
    assert fleet.returncode == 0, fleet.stderr
    mode = (tmp_path / "npx.log.mode").read_text(encoding="utf-8")
    assert mode.strip() == "browsers-only"
    log = (tmp_path / "npx.log").read_text(encoding="utf-8")
    assert "install-deps chromium --dry-run" in log.replace("\n", " ")


def test_install_script_fails_with_action_error_when_deps_missing(tmp_path: Path) -> None:
    fleet = _run_install_script(tmp_path, runner_environment="self-hosted", deps_dry_run_exit=1)
    assert fleet.returncode == 1
    assert "::error::" in fleet.stdout
    assert "sudo npx playwright install-deps chromium" in fleet.stdout
    assert "self-hosted fleet runner" in fleet.stdout


def test_frontend_playwright_jobs_use_shared_install_script() -> None:
    workflow = yaml.safe_load(_FRONTEND_TESTS.read_text(encoding="utf-8"))
    for job_id in ("playwright-e2e", "staff-console-e2e"):
        steps = workflow["jobs"][job_id]["steps"]
        install_steps = [s for s in steps if "Playwright browsers" in (s.get("name") or "")]
        assert len(install_steps) == 1, job_id
        assert "ci_install_playwright_browsers.sh chromium" in install_steps[0]["run"]
