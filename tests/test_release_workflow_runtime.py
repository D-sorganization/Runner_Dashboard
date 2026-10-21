"""Behavioral tests for the release workflow's inline Bash steps."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from bash_host import as_bash_path, require_bash
from test_release_workflow_yaml import RELEASE_WORKFLOW, _load_workflow, _release_tarball_step


def _run_workflow_step(
    step: dict, cwd: Path, output_file: Path, env: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    output_file.write_text("", encoding="utf-8")
    result = subprocess.run(
        [require_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=cwd,
        env={
            "PATH": os.environ.get("PATH", ""),
            "GITHUB_OUTPUT": as_bash_path(output_file),
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    outputs = dict(line.split("=", 1) for line in output_file.read_text(encoding="utf-8").splitlines() if "=" in line)
    return result, outputs


@pytest.mark.parametrize(
    ("event_name", "ref", "input_version", "input_dry_run", "file_version", "expected"),
    [
        (
            "workflow_dispatch",
            "refs/heads/main",
            "8.1.0",
            "true",
            "8.1.0",
            {"version": "8.1.0", "dry_run": "true", "tag": "v8.1.0"},
        ),
        (
            "push",
            "refs/tags/v8.2.0",
            "",
            "false",
            "8.1.0",
            {"version": "8.2.0", "dry_run": "false", "tag": "v8.2.0"},
        ),
        (
            "push",
            "refs/heads/main",
            "",
            "false",
            "8.3.0",
            {"version": "8.3.0", "dry_run": "false", "tag": "v8.3.0"},
        ),
    ],
    ids=("manual", "tag-push", "version-push"),
)
def test_release_resolve_step_preserves_all_outputs(
    tmp_path: Path,
    event_name: str,
    ref: str,
    input_version: str,
    input_dry_run: str,
    file_version: str,
    expected: dict[str, str],
) -> None:
    steps = _load_workflow(RELEASE_WORKFLOW)["jobs"]["release"]["steps"]
    step = next(step for step in steps if step.get("name") == "Resolve target version and dry-run flag")
    result, outputs = _run_workflow_step(
        step,
        tmp_path,
        tmp_path / "github-output",
        {
            "EVENT_NAME": event_name,
            "REF": ref,
            "INPUT_VERSION": input_version,
            "INPUT_DRY_RUN": input_dry_run,
            "FILE_VERSION": file_version,
        },
    )
    assert result.returncode == 0, result.stderr
    assert outputs == expected


@pytest.mark.parametrize(
    ("checked_out_sha", "event_sha"),
    [
        ("b" * 40, "b" * 40),  # ordinary release: source and event are the same
        ("a" * 40, "b" * 40),  # recovery: checked-out tag differs from dispatch SHA
    ],
    ids=("normal-release", "existing-tag-recovery"),
)
def test_release_tarball_step_packages_the_recorded_checkout_sha(
    tmp_path: Path, checked_out_sha: str, event_sha: str
) -> None:
    """Run the actual workflow Bash step with a stub packager that writes metadata."""
    step = _release_tarball_step()
    output_dir = tmp_path / "runner-temp"
    output_file = tmp_path / "github-output"
    fake_packager = tmp_path / "deploy" / "package-dashboard-artifact.sh"
    fake_packager.parent.mkdir()
    fake_packager.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
OUTPUT_DIR=
VERSION=
SHA=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --sha) SHA="$2"; shift 2 ;;
        --python-minor) shift 2 ;;
        *) echo "unexpected packager argument: $1" >&2; exit 2 ;;
    esac
done
mkdir -p "$OUTPUT_DIR"
printf '{"git_sha":"%s"}\\n' "$SHA" > "$OUTPUT_DIR/deployment.json"
ARTIFACT="$OUTPUT_DIR/dashboard-${VERSION}.tar.gz"
printf 'artifact for %s\\n' "$SHA" > "$ARTIFACT"
(cd "$OUTPUT_DIR" && sha256sum "$(basename "$ARTIFACT")" > "$(basename "$ARTIFACT").sha256")
touch "$OUTPUT_DIR/dashboard-${VERSION}.bundle"
""",
        encoding="utf-8",
    )

    step_env = step.get("env", {})
    assert step_env.get("SOURCE_SHA") == "${{ steps.source.outputs.sha }}"
    result, outputs = _run_workflow_step(
        step,
        tmp_path,
        output_file,
        {
            "RUNNER_TEMP": as_bash_path(output_dir),
            "GITHUB_SHA": event_sha,
            "VERSION": "8.1.0",
            "ARTIFACT_PYTHON_MINOR": "3.12",
            "SOURCE_SHA": checked_out_sha,
        },
    )

    assert result.returncode == 0, result.stderr
    metadata = json.loads((output_dir / "release-artifacts" / "deployment.json").read_text(encoding="utf-8"))
    assert metadata["git_sha"] == checked_out_sha
    assert outputs["artifact"] == as_bash_path(output_dir / "release-artifacts" / "dashboard-8.1.0.tar.gz")
    assert outputs["checksum"].endswith("dashboard-8.1.0.tar.gz.sha256")
    assert outputs["bundle"].endswith("dashboard-8.1.0.bundle")
    assert len(outputs["sha256"]) == 64
