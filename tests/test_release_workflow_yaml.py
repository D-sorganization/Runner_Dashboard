"""tests/test_release_workflow_yaml.py — release workflow contract tests (issue #431).

Validates `.github/workflows/release.yml` and `.github/workflows/verify-tag.yml`:

  1. Both workflow files exist.
  2. Both workflows route to a `d-sorg-fleet*` self-hosted runner label (not ubuntu-latest).
  3. Every `uses:` reference is pinned to a 40-char SHA.
  4. release.yml exposes both `workflow_dispatch` and `push` triggers.
  5. release.yml defines the `dry_run` input.
  6. verify-tag.yml triggers on tag pushes matching `v*`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from bash_host import as_bash_path, require_bash

ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
VERIFY_TAG_WORKFLOW = ROOT / ".github" / "workflows" / "verify-tag.yml"

# A SHA-pinned action ref looks like: owner/name@<40-hex-sha>  (optional comment).
SHA_PINNED_RE = re.compile(r"^[\w.\-]+/[\w.\-]+(?:/[\w.\-/]+)?@[0-9a-f]{40}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_workflow(path: Path) -> dict:  # type: ignore[type-arg]
    """Parse a workflow YAML, normalizing the `on:` truthy quirk.

    PyYAML maps the bare key `on` to the boolean True. Keep both spellings
    available so tests can index either.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} did not parse to a mapping"
    if True in data and "on" not in data:
        data["on"] = data[True]
    return data


def _all_uses(path: Path) -> list[str]:
    """Return every `uses:` value in the workflow text, line-by-line."""
    uses: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("uses:"):
            value = stripped.split(":", 1)[1].strip()
            # Drop any trailing inline comment.
            value = value.split("#", 1)[0].strip()
            # Strip surrounding quotes if present.
            value = value.strip("'\"")
            uses.append(value)
    return uses


# ---------------------------------------------------------------------------
# Existence
# ---------------------------------------------------------------------------


def test_release_workflow_exists() -> None:
    assert RELEASE_WORKFLOW.is_file(), f"Expected release workflow at {RELEASE_WORKFLOW.relative_to(ROOT)}"


def test_verify_tag_workflow_exists() -> None:
    assert VERIFY_TAG_WORKFLOW.is_file(), f"Expected verify-tag workflow at {VERIFY_TAG_WORKFLOW.relative_to(ROOT)}"


# ---------------------------------------------------------------------------
# runs-on: d-sorg-fleet*
# ---------------------------------------------------------------------------


def _runs_on_values(path: Path) -> list[str]:
    data = _load_workflow(path)
    jobs = data.get("jobs", {})
    assert jobs, f"{path} has no jobs"
    values: list[str] = []
    for job_name, job in jobs.items():
        assert isinstance(job, dict), f"job {job_name} in {path} is not a mapping"
        runs_on = job.get("runs-on")
        assert runs_on is not None, f"job {job_name} in {path} has no runs-on"
        values.append(str(runs_on))
    return values


def _assert_d_sorg_fleet_runner(path: Path, runs_on: str) -> None:
    assert runs_on.startswith("d-sorg-fleet"), (
        f"{path.name} job runs-on={runs_on!r}; must stay on a d-sorg-fleet runner label"
    )


def test_release_workflow_uses_d_sorg_fleet() -> None:
    values = _runs_on_values(RELEASE_WORKFLOW)
    assert values, "release.yml has no jobs"
    for v in values:
        _assert_d_sorg_fleet_runner(RELEASE_WORKFLOW, v)


def test_verify_tag_workflow_uses_d_sorg_fleet() -> None:
    values = _runs_on_values(VERIFY_TAG_WORKFLOW)
    assert values, "verify-tag.yml has no jobs"
    for v in values:
        _assert_d_sorg_fleet_runner(VERIFY_TAG_WORKFLOW, v)


# ---------------------------------------------------------------------------
# SHA-pinned actions
# ---------------------------------------------------------------------------


def test_release_workflow_actions_sha_pinned() -> None:
    refs = _all_uses(RELEASE_WORKFLOW)
    assert refs, "release.yml does not reference any actions"
    for ref in refs:
        assert SHA_PINNED_RE.match(ref), (
            f"release.yml action ref not SHA-pinned: {ref!r} (expected owner/name@<40-hex-sha>)"
        )


def test_verify_tag_workflow_actions_sha_pinned() -> None:
    refs = _all_uses(VERIFY_TAG_WORKFLOW)
    assert refs, "verify-tag.yml does not reference any actions"
    for ref in refs:
        assert SHA_PINNED_RE.match(ref), (
            f"verify-tag.yml action ref not SHA-pinned: {ref!r} (expected owner/name@<40-hex-sha>)"
        )


# ---------------------------------------------------------------------------
# Triggers and inputs
# ---------------------------------------------------------------------------


def test_release_workflow_has_workflow_dispatch_and_push_triggers() -> None:
    data = _load_workflow(RELEASE_WORKFLOW)
    triggers = data["on"]
    assert isinstance(triggers, dict), f"release.yml `on:` must be a mapping, got {type(triggers).__name__}"
    assert "workflow_dispatch" in triggers, "release.yml must define a workflow_dispatch trigger"
    assert "push" in triggers, "release.yml must define a push trigger"


def test_release_workflow_push_trigger_watches_version_file() -> None:
    data = _load_workflow(RELEASE_WORKFLOW)
    push_cfg = data["on"]["push"]
    assert isinstance(push_cfg, dict), "release.yml push trigger must be a mapping"
    paths = push_cfg.get("paths") or []
    assert "VERSION" in paths, "release.yml push trigger should watch VERSION (auto-release on bump)"


def test_release_workflow_defines_dry_run_input() -> None:
    data = _load_workflow(RELEASE_WORKFLOW)
    dispatch = data["on"]["workflow_dispatch"]
    assert isinstance(dispatch, dict), "release.yml workflow_dispatch must be a mapping with inputs"
    inputs = dispatch.get("inputs") or {}
    assert "dry_run" in inputs, "release.yml workflow_dispatch must define a `dry_run` input"
    assert "version" in inputs, "release.yml workflow_dispatch must define a `version` input"
    assert inputs["version"].get("required") is True, "`version` input must be required"
    assert inputs["recover_existing_tag"].get("type") == "boolean"
    assert inputs["recover_existing_tag"].get("default") is False


def _release_tarball_step() -> dict:
    steps = _load_workflow(RELEASE_WORKFLOW)["jobs"]["release"]["steps"]
    return next(step for step in steps if step.get("name") == "Build release tarball")


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


def test_release_workflow_passes_actionlint_when_available() -> None:
    actionlint = shutil.which("actionlint")
    if actionlint is None:
        pytest.skip("actionlint is not installed")

    result = subprocess.run(
        [actionlint, str(RELEASE_WORKFLOW.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_release_uses_canonical_schema_v2_packager() -> None:
    step = _release_tarball_step()
    assert "bash deploy/package-dashboard-artifact.sh \\" in step["run"]
    assert '--output-dir "$OUTPUT_DIR" \\' in step["run"]
    assert '--version "$VERSION" \\' in step["run"]
    assert step["env"]["SOURCE_SHA"] == "${{ steps.source.outputs.sha }}"
    assert '--sha "$SOURCE_SHA"' in step["run"]
    assert '--sha "$GITHUB_SHA"' not in step["run"]
    assert "bash deploy/write-deployment-metadata.sh" not in step["run"]
    assert 'tar czf "$TMP_ARTIFACT"' not in step["run"]


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


def test_release_publication_does_not_depend_on_checkout_discovery() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert 'gh release create "$TAG" \\' in text
    assert '--repo "$GITHUB_REPOSITORY" \\' in text


def test_release_workflow_uses_cosign_bundle_artifact() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert '--bundle "$BUNDLE_FILE"' in text
    assert 'BUNDLE_FILE="$OUTPUT_DIR/dashboard-${VERSION}.bundle"' in text
    assert "BUNDLE_FILE: ${{ steps.tarball.outputs.bundle }}" in text
    assert '"$BUNDLE_FILE" \\' in text
    assert "--output-signature" not in text
    assert "--output-certificate" not in text


def test_release_workflow_recovers_only_an_exact_governed_tag() -> None:
    """A late publish failure may reuse only the exact tag created by this workflow."""
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert 'git fetch --force origin "refs/tags/$TAG:refs/tags/$TAG"' in text
    assert 'TAG_COMMIT=$(git rev-parse "$TAG^{commit}")' in text
    assert 'if [ "$TAG_COMMIT" != "$SOURCE_SHA" ]; then' in text
    assert 'OBJ_TYPE=$(git cat-file -t "$TAG")' in text
    assert 'grep -Fq "$AUTO_NOTES_HEADER"' in text
    assert 'echo "tag_exists=true" >> "$GITHUB_OUTPUT"' in text
    assert 'if [ "$ALLOW_EXISTING_TAG" != "true" ]; then' in text
    assert 'if [ "$RECOVER_EXISTING_TAG" = "true" ]; then' in text


def test_release_workflow_skips_tag_mutation_during_recovery() -> None:
    """Recovery must not recreate or repush an already-qualified tag."""
    data = _load_workflow(RELEASE_WORKFLOW)
    steps = data["jobs"]["release"]["steps"]
    by_name = {step.get("name"): step for step in steps if isinstance(step, dict)}

    expected_guard = "steps.resolve.outputs.dry_run != 'true' && steps.tag_state.outputs.tag_exists != 'true'"
    assert by_name["Create unsigned annotated tag"]["if"] == expected_guard
    assert by_name["Push tag to origin"]["if"] == expected_guard


def test_release_workflow_uses_explicit_repo_for_release_publication() -> None:
    """gh must not rediscover the repository through a WSL UNC worktree path."""
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert 'gh release create "$TAG" \\' in text
    assert '--repo "$GITHUB_REPOSITORY" \\' in text
    assert '--target "${{ steps.source.outputs.sha }}" \\' in text


def test_manual_tag_recovery_checks_out_the_tagged_source() -> None:
    data = _load_workflow(RELEASE_WORKFLOW)
    inputs = data["on"]["workflow_dispatch"]["inputs"]
    assert "recover_existing_tag" in inputs
    checkout = data["jobs"]["release"]["steps"][0]
    assert checkout["with"]["ref"] == (
        "${{ inputs.recover_existing_tag && format('refs/tags/v{0}', inputs.version) || github.sha }}"
    )
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "SOURCE_SHA=$(git rev-parse HEAD)" in text
    assert 'echo "sha=$SOURCE_SHA" >> "$GITHUB_OUTPUT"' in text


def test_release_notes_exclude_recovered_current_tag_from_previous_tag() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert 'grep -Fxv "$TAG"' in text


def test_verify_tag_workflow_triggers_on_v_tag_push() -> None:
    data = _load_workflow(VERIFY_TAG_WORKFLOW)
    triggers = data["on"]
    assert isinstance(triggers, dict), "verify-tag.yml `on:` must be a mapping"
    push_cfg = triggers.get("push")
    assert isinstance(push_cfg, dict), "verify-tag.yml must trigger on push"
    tag_patterns = push_cfg.get("tags") or []
    assert "v*" in tag_patterns, "verify-tag.yml push trigger must include the `v*` tag pattern"
