#!/usr/bin/env python3
"""Create deployment metadata tied to the clean checkout used for packaging."""

from __future__ import annotations

import argparse
import hashlib
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
_MAX_STATIC_ASSET_FILES = 512
_MAX_STATIC_ASSET_FILE_BYTES = 32 * 1024 * 1024
_MAX_STATIC_ASSET_TOTAL_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactBuildInputs:
    """Validated inputs describing the source and compatibility of a build."""

    repo: Path
    version: str
    python_minor: str
    sha_override: str = ""
    static_assets_dir: Path | None = None


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


def build_static_asset_manifest(dist_dir: Path) -> dict[str, object]:
    """Return a bounded, relative-path inventory of packaged static bytes."""
    try:
        root = dist_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError("static asset directory is unavailable") from exc
    if not root.is_dir():
        raise ValueError("static asset directory is not a directory")

    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("static asset manifest refuses symlink entries")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("static asset manifest contains an unsafe path")
        if len(entries) >= _MAX_STATIC_ASSET_FILES:
            raise ValueError(f"static asset manifest exceeds {_MAX_STATIC_ASSET_FILES} entries")

        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError("static asset changed while building manifest") from exc
        if size > _MAX_STATIC_ASSET_FILE_BYTES:
            raise ValueError("static asset exceeds per-file manifest limit")
        total_bytes += size
        if total_bytes > _MAX_STATIC_ASSET_TOTAL_BYTES:
            raise ValueError("static assets exceed total manifest limit")

        digest = hashlib.sha256()
        bytes_read = 0
        try:
            with path.open("rb") as stream:
                while bytes_read < size:
                    chunk = stream.read(min(1024 * 1024, size - bytes_read))
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    digest.update(chunk)
            current_size = path.stat().st_size
        except OSError as exc:
            raise ValueError("static asset changed while building manifest") from exc
        if bytes_read != size or current_size != size:
            raise ValueError("static asset changed while building manifest")
        entries.append({"path": relative.as_posix(), "size_bytes": size, "sha256": digest.hexdigest()})

    if not entries:
        raise ValueError("static asset directory contains no files")
    return {"schema": "runner-dashboard-static-assets-v1", "entries": entries}


def build_metadata(
    inputs: ArtifactBuildInputs,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return compatible metadata only when its SHA names this clean checkout."""
    env = os.environ if environ is None else environ
    sha, branch = validate_source_identity(inputs.repo, sha_override=inputs.sha_override)
    python_minor = inputs.python_minor
    metadata: dict[str, object] = {
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
    if inputs.static_assets_dir is not None:
        metadata["static_asset_manifest"] = build_static_asset_manifest(inputs.static_assets_dir)
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--python-minor")
    parser.add_argument("--sha", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--static-assets-dir", type=Path)
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
        inputs = ArtifactBuildInputs(args.repo_root, args.version, args.python_minor, args.sha, args.static_assets_dir)
        metadata = build_metadata(inputs)
    except ValueError as exc:
        sys.stderr.write(f"artifact metadata: {exc}\n")
        return 1
    args.output.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
