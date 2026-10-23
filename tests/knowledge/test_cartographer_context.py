from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from knowledge_pack.cartographer import (  # noqa: E402
    _format_reference,
    build_cartographer_pack,
    estimate_context_tokens,
    preload_panel_context,
)
from knowledge_pack.pack import Passage  # noqa: E402


def _repo(root: Path) -> Path:
    docs = {
        "staff/standards/FLEET.md": "# Fleet invariants\n\nFleet agents preserve read-only source boundaries.\n",
        "AGENTS.md": "# Invariants\n\nPanel seats are read-only and requests have a bounded topic.\n",
        "docs/architecture/C4.md": (
            "# Module map\n\nThe public staff panel endpoint is POST /api/v1/staff/panels.\n\n"
            "## Public contracts\n\nPanelCreateRequest checks three to four read-only expert seats.\n"
        ),
        "docs/adr/0008-context.md": (
            "# ADR 0008: Panel context\n\n## Decision\n\nPanel context is bounded and reference-only.\n"
        ),
    }
    for rel, text in docs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return root


@pytest.mark.unit
def test_cartographer_context_is_cited_relevant_and_within_budget(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "Runner_Dashboard")
    pack_path = tmp_path / "knowledge" / "cartographer.sqlite"

    info = build_cartographer_pack({"Runner_Dashboard": repo}, pack_path)
    context = preload_panel_context(
        {"Runner_Dashboard": repo},
        "How does POST /api/v1/staff/panels validate read-only seats and preserve invariants?",
        max_tokens=800,
        knowledge_dir=pack_path.parent,
    )

    assert info.files == 4
    assert "Public contracts" in context or "read-only expert seats" in context
    assert ":codebase:Runner_Dashboard@" in context
    assert re.search(r"@[0-9a-f]{8}:docs/[^\]]+#L\d+-L\d+\]", context)
    assert estimate_context_tokens(context) <= 800


@pytest.mark.unit
def test_cartographer_rejects_invalid_token_budgets(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "Runner_Dashboard")
    with pytest.raises(ValueError, match="max_tokens"):
        preload_panel_context({"Runner_Dashboard": repo}, "panel", max_tokens=0, knowledge_dir=tmp_path)


@pytest.mark.unit
def test_default_context_includes_map_invariants_contracts_and_recent_adr(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "Runner_Dashboard")
    context = preload_panel_context(
        {"Runner_Dashboard": repo},
        "panel API contract and read-only expert invariants",
        knowledge_dir=tmp_path / "knowledge",
    )

    assert "docs/architecture/C4.md" in context
    assert "AGENTS.md" in context
    assert "FLEET.md" in context
    assert "backend" in context or "Public contracts" in context
    assert "docs/adr/0008-context.md" in context
    assert estimate_context_tokens(context) <= 800


@pytest.mark.unit
def test_missing_promised_blob_never_runs_remote_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "Runner_Dashboard")
    source = "docs/architecture/C4.md"
    text = (repo / source).read_text(encoding="utf-8")
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{commit}:{source}"], check=True, capture_output=True, text=True
    ).stdout.strip()
    object_path = repo / ".git" / "objects" / blob[:2] / blob[2:]
    assert object_path.is_file()
    object_path.unlink()
    subprocess.run(["git", "-C", str(repo), "config", "extensions.partialClone", "origin"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "remote.origin.promisor", "true"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", "evil::payload"], check=True)

    helper_dir = tmp_path / "helpers"
    helper_dir.mkdir()
    marker = tmp_path / "helper-ran"
    helper = helper_dir / "git-remote-evil"
    helper.write_text(f"#!/bin/sh\nprintf invoked > '{marker}'\nexit 1\n", encoding="utf-8")
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{helper_dir}{os.pathsep}{os.environ['PATH']}")

    passage = Passage(
        repo="Runner_Dashboard",
        source=source,
        anchor="module-map",
        title="Module map",
        text=text,
        commit=commit,
        content_hash="0" * 64,
        status="current",
        authority="published",
        score=0.0,
    )

    assert _format_reference(passage, repo) is None
    assert not marker.exists()


def test_all_six_repositories_are_queryable_and_dirty_text_is_not_cited(tmp_path: Path) -> None:
    from knowledge_pack.pack import KnowledgePack

    names = ("UpstreamDrift", "AffineDrift", "Golf_Model", "Tools", "Repository_Management", "Runner_Dashboard")
    repos = {name: _repo(tmp_path / name) for name in names}
    output = tmp_path / "knowledge" / "cartographer.sqlite"
    build_cartographer_pack(repos, output)
    pack = KnowledgePack.open(output)
    assert set(pack.info().commits) == set(names)
    assert {hit.repo for hit in pack.search("public staff panel", k=100)} == set(names)
    root = repos["Runner_Dashboard"]
    (root / "docs/architecture/C4.md").write_text("Uncommitted confidential architecture override")
    build_cartographer_pack({"Runner_Dashboard": root}, output)
    context = preload_panel_context({"Runner_Dashboard": root}, "confidential", knowledge_dir=output.parent)
    assert "confidential" not in context


def test_unicode_context_budget_is_a_conservative_token_bound() -> None:
    text = "漢字🚀!?" * 100
    assert estimate_context_tokens(text) >= len(text.encode("utf-8"))


def test_pack_does_not_index_a_symlink_outside_registered_roots(tmp_path: Path) -> None:
    from knowledge_pack.pack import KnowledgePack

    root = _repo(tmp_path / "Runner_Dashboard")
    secret = tmp_path / "outside.md"
    secret.write_text("outside confidential uniqueprobe")
    (root / "docs/architecture/escape.md").symlink_to(secret)
    output = tmp_path / "cartographer.sqlite"
    build_cartographer_pack({"Runner_Dashboard": root}, output)
    assert KnowledgePack.open(output).search("uniqueprobe", k=5) == []
