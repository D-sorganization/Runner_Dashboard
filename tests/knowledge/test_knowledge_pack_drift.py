"""Tests for vendored knowledge-pack engine and drift against Tools source.

Vendored from Tools commit 09ff428af314969363f8908dcebafb84ddd7a3ef.
Path in Tools: src/shared/python/ai/knowledge/
(Tools#5345, Repository_Management#1772, Runner_Dashboard#1479).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from knowledge_pack import (  # noqa: E402
    AUTHORITIES,
    FORMAT_VERSION,
    HIDDEN_STATUSES,
    STATUSES,
    KnowledgePack,
    Passage,
    build_pack,
    chunk_document,
    load_manifest,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_REPOS = [
    _REPO_ROOT.parent / "Tools",
    _REPO_ROOT.parent.parent / "Tools",
]
PINNED_TOOLS_SHA = "09ff428af314969363f8908dcebafb84ddd7a3ef"  # pragma: allowlist secret

MODULE_FILES = (
    "__init__.py",
    "__main__.py",
    "chunking.py",
    "cli.py",
    "manifest.py",
    "pack.py",
    "sources.py",
)


def _tools_repo() -> Path | None:
    for cand in _CANDIDATE_REPOS:
        if (cand / ".git").exists():
            return cand
    return None


def test_vendored_api_symbols() -> None:
    """Verify all expected public symbols are exposed by knowledge_pack."""
    assert FORMAT_VERSION == 1
    assert "published" in AUTHORITIES
    assert "current" in STATUSES
    assert "superseded" in HIDDEN_STATUSES
    assert callable(build_pack)
    assert callable(chunk_document)
    assert callable(load_manifest)
    assert hasattr(KnowledgePack, "open")
    assert hasattr(KnowledgePack, "search")
    assert hasattr(KnowledgePack, "info")
    assert hasattr(KnowledgePack, "is_stale")


def test_passage_citation_property() -> None:
    p = Passage(
        repo="UpstreamDrift",
        source="docs/research/energy.md",
        anchor="timing",
        title="Energy Timing",
        text="Sample text",
        commit="0123456789abcdef",
        content_hash="abc",
        status="current",
        authority="findings",
        score=1.5,
    )
    assert p.citation == "UpstreamDrift:docs/research/energy.md#timing @ 01234567"


def test_knowledge_pack_drift_against_pinned_tools_sha() -> None:
    """Drift guard: vendored modules must match Tools commit 09ff428af314969363f8908dcebafb84ddd7a3ef."""
    repo = _tools_repo()
    if repo is None:
        pytest.skip("no Tools repository found beside this worktree")

    vendored_dir = _BACKEND / "knowledge_pack"
    assert vendored_dir.is_dir(), "backend/knowledge_pack directory missing"

    for filename in MODULE_FILES:
        vendored_file = vendored_dir / filename
        assert vendored_file.is_file(), f"Vendored file {filename} is missing"

        # Check pinned git commit first, fallback to working tree
        src_path = f"src/shared/python/ai/knowledge/{filename}"
        try:
            res = subprocess.run(
                ["git", "-C", str(repo), "show", f"{PINNED_TOOLS_SHA}:{src_path}"],
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
            )
            source_raw = res.stdout
        except (subprocess.CalledProcessError, OSError):
            disk_file = repo / src_path
            if disk_file.is_file():
                source_raw = disk_file.read_text(encoding="utf-8")
            else:
                pytest.fail(f"Could not read {src_path} from Tools at {PINNED_TOOLS_SHA}")

        # Strip header comment lines (added during vendoring) and the local
        # `# fmt: off` / `# fmt: on` formatter directives (added so `ruff format`
        # does not reflow files that must stay byte-identical to Tools) before
        # comparing.
        vendored_raw = vendored_file.read_text(encoding="utf-8")
        vendored_body = "\n".join(
            line
            for line in vendored_raw.splitlines()
            if not (
                line.startswith("# Vendored")
                or line.startswith("# Path in Tools")
                or line.startswith("# Part of D-sorganization")
                or line.strip() in ("# fmt: off", "# fmt: on")
            )
        )
        assert vendored_body.strip() == source_raw.strip(), (
            f"Drift detected in {filename} against Tools {PINNED_TOOLS_SHA}"
        )
