#!/usr/bin/env python3
"""Create deployment metadata tied to the clean checkout used for packaging."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class ArtifactBuildInputs:
    """Validated inputs describing the source and compatibility of a build."""

    repo: Path
    version: str
    python_minor: str
    sha_override: str = ""


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"cannot establish artifact source identity: git {args[0]} failed") from exc
    return result.stdout.strip()


def validate_source_identity(repo: Path, *, sha_override: str = "") -> tuple[str, str]:
    """Return the current commit and branch only for a clean, matching checkout."""
    sha = _git(repo, "rev-parse", "HEAD^{commit}")
    if not _FULL_SHA.fullmatch(sha):
        raise ValueError("checkout HEAD did not resolve to a full 40-character commit SHA")
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("artifact source checkout is dirty; refusing immutable artifact packaging")

    if sha_override:
        if not _FULL_SHA.fullmatch(sha_override):
            raise ValueError("--sha must be a full 40-character checkout SHA")
        if sha_override.lower() != sha.lower():
            raise ValueError(f"--sha does not match checkout HEAD ({sha})")

    return sha.lower(), _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"


def build_metadata(
    inputs: ArtifactBuildInputs,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return compatible metadata only when its SHA names this clean checkout."""
    env = os.environ if environ is None else environ
    sha, branch = validate_source_identity(inputs.repo, sha_override=inputs.sha_override)
    python_minor = inputs.python_minor
    return {
        "app": "runner-dashboard",
        "version": inputs.version,
        "git_sha": sha,
        "git_branch": branch,
        "git_dirty": False,
        "build_timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "artifact-build",
        "build_context": "github-actions" if env.get("GITHUB_ACTIONS", "").lower() == "true" else "local",
        "build_context_evidence": "environment-hint-unverified",
        "compatibility": {
            "artifact_schema": "runner-dashboard-artifact-v2",
            "python_requires": ">=3.11,<3.14",
            "python_minor": python_minor,
            "service_name": "runner-dashboard.service",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--python-minor")
    parser.add_argument("--sha", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-source-only", action="store_true")
    parser.add_argument("--print-sha", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate_source_only:
            sha, _ = validate_source_identity(args.repo_root, sha_override=args.sha)
            if args.print_sha:
                sys.stdout.write(f"{sha}\n")
            return 0
        if not args.version or not args.python_minor or not args.output:
            parser.error("--version, --python-minor, and --output are required for metadata generation")
        inputs = ArtifactBuildInputs(args.repo_root, args.version, args.python_minor, args.sha)
        metadata = build_metadata(inputs)
    except ValueError as exc:
        sys.stderr.write(f"artifact metadata: {exc}\n")
        return 1
    args.output.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
