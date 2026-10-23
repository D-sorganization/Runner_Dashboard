"""Source identity and build-context metadata for packaged artifacts (#2081)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
METADATA = ROOT / "deploy" / "artifact_metadata.py"
SPEC = importlib.util.spec_from_file_location("artifact_metadata", METADATA)
assert SPEC and SPEC.loader
artifact_metadata = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = artifact_metadata
SPEC.loader.exec_module(artifact_metadata)


def _build_inputs(repo: Path, sha_override: str = "") -> artifact_metadata.ArtifactBuildInputs:
    if sha_override:
        return artifact_metadata.ArtifactBuildInputs(repo, "1.2.3", "3.12", sha_override)
    return artifact_metadata.ArtifactBuildInputs(repo, "1.2.3", "3.12")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Artifact Test"], check=True)
    (repo / "source.txt").write_text("committed source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "source.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def test_local_artifact_metadata_uses_clean_checkout_identity(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    inputs = _build_inputs(repo)

    metadata = artifact_metadata.build_metadata(inputs, environ={})

    assert metadata["git_sha"] == sha
    assert metadata["git_dirty"] is False
    assert metadata["source"] == "artifact-build"
    assert metadata["build_context"] == "local"
    assert metadata["build_context_evidence"] == "environment-hint-unverified"
    assert metadata["build_timestamp"].endswith("Z")
    assert metadata["compatibility"]["artifact_schema"] == "runner-dashboard-artifact-v2"
    with pytest.raises(FrozenInstanceError):
        inputs.version = "2.0.0"


def test_sha_override_must_be_full_checkout_sha(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)

    with pytest.raises(ValueError, match="full 40-character checkout SHA"):
        artifact_metadata.build_metadata(_build_inputs(repo, sha[:12]), environ={})


def test_sha_override_must_match_checkout(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)

    with pytest.raises(ValueError, match="does not match checkout HEAD"):
        artifact_metadata.build_metadata(_build_inputs(repo, "f" * 40), environ={})


def test_release_recovery_uses_explicit_tag_checkout_not_event_sha(tmp_path: Path) -> None:
    repo, tag_checkout_sha = _repo(tmp_path)
    event_sha = "f" * 40

    metadata = artifact_metadata.build_metadata(
        _build_inputs(repo, tag_checkout_sha),
        environ={"GITHUB_ACTIONS": "true", "GITHUB_SHA": event_sha},
    )

    assert metadata["git_sha"] == tag_checkout_sha
    assert metadata["build_context"] == "github-actions"


def test_release_recovery_rejects_event_sha_when_passed_as_explicit_override(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    event_sha = "f" * 40

    with pytest.raises(ValueError, match="--sha does not match checkout HEAD"):
        artifact_metadata.build_metadata(
            _build_inputs(repo, event_sha),
            environ={"GITHUB_ACTIONS": "true", "GITHUB_SHA": event_sha},
        )


def test_final_metadata_check_rejects_head_changed_after_initial_identity(tmp_path: Path) -> None:
    repo, initial_sha = _repo(tmp_path)
    (repo / "source.txt").write_text("different committed source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "source moved during build"], check=True)

    with pytest.raises(ValueError, match="--sha does not match checkout HEAD"):
        artifact_metadata.build_metadata(_build_inputs(repo, initial_sha), environ={})


def test_dirty_checkout_fails_closed_even_when_untracked_files_are_present(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    (repo / "untracked.txt").write_text("not in source commit\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checkout is dirty"):
        artifact_metadata.build_metadata(_build_inputs(repo), environ={})


def test_github_actions_environment_is_only_an_unverified_context_hint(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)

    metadata = artifact_metadata.build_metadata(_build_inputs(repo), environ={"GITHUB_ACTIONS": "true"})

    assert metadata["build_context"] == "github-actions"
    assert metadata["build_context_evidence"] == "environment-hint-unverified"
    assert metadata["source"] == "artifact-build"


def test_metadata_cli_preserves_sha_flags(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    output = tmp_path / "deployment.json"
    generated = subprocess.run(
        [
            sys.executable,
            str(METADATA),
            "--repo-root",
            str(repo),
            "--version",
            "1.2.3",
            "--python-minor",
            "3.12",
            "--sha",
            sha,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["git_sha"] == sha

    validated = subprocess.run(
        [
            sys.executable,
            str(METADATA),
            "--repo-root",
            str(repo),
            "--sha",
            sha,
            "--validate-source-only",
            "--print-sha",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    assert validated.stdout == f"{sha}\n"
