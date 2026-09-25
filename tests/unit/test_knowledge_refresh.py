"""Unit tests for staff knowledge pack refresh mechanism (Issue #1479)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from knowledge_pack import KnowledgePack  # noqa: E402
from staff.knowledge_refresh import (  # noqa: E402
    find_knowledge_manifests,
    refresh_all_packs,
    refresh_pack,
)


@pytest.fixture
def fixture_corpus(tmp_path: Path) -> dict[str, Path]:
    """Create a minimal fixture repository and knowledge manifest."""
    # 1. Fake repository: UpstreamDrift
    repo_dir = tmp_path / "Repositories" / "UpstreamDrift"
    docs_dir = repo_dir / "docs" / "research"
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_file = docs_dir / "findings.md"
    doc_file.write_text(
        "# Energy Transfer\n\n"
        "## Proximal Distal Sequence\n\n"
        "Our findings show that energy transfer peaks at release.\n",
        encoding="utf-8",
    )

    # Initialize fake git repo if needed or just directory
    (repo_dir / ".git").mkdir()

    # 2. Fake RM knowledge manifest
    rm_dir = tmp_path / "Repositories" / "Repository_Management"
    knowledge_manifests_dir = rm_dir / "staff" / "knowledge"
    knowledge_manifests_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = knowledge_manifests_dir / "findings.yml"
    manifest_file.write_text(
        "id: findings\n"
        "title: UpstreamDrift findings\n"
        "chunk_chars: 1800\n"
        "sources:\n"
        "  - repo: UpstreamDrift\n"
        "    authority: findings\n"
        "    include:\n"
        "      - 'docs/research/**/*.md'\n"
        "status_overrides: {}\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "knowledge_store"
    out_dir.mkdir(parents=True, exist_ok=True)

    return {
        "repo": repo_dir,
        "doc": doc_file,
        "rm": rm_dir,
        "manifest": manifest_file,
        "out": out_dir,
    }


def test_find_knowledge_manifests(fixture_corpus: dict[str, Path]) -> None:
    manifests = find_knowledge_manifests(rm_root=fixture_corpus["rm"])
    assert len(manifests) == 1
    assert manifests[0].name == "findings.yml"


def test_refresh_pack_builds_and_skips_when_fresh(fixture_corpus: dict[str, Path]) -> None:
    manifest_path = fixture_corpus["manifest"]
    roots = {"UpstreamDrift": fixture_corpus["repo"]}
    out_dir = fixture_corpus["out"]

    # 1. Initial build: pack does not exist, so it should build
    rebuilt, info = refresh_pack(manifest_path, roots=roots, knowledge_dir=out_dir)
    assert rebuilt is True
    assert info is not None
    assert info.pack_id == "findings"
    assert info.passages >= 1

    pack_file = out_dir / "findings.sqlite"
    assert pack_file.is_file()

    # Verify search works
    pack = KnowledgePack.open(pack_file)
    hits = pack.search("energy transfer")
    assert len(hits) >= 1
    assert "energy transfer" in hits[0].text.lower()

    # 2. Second build without file changes: is_stale is False, so skip rebuilding
    rebuilt_again, info_again = refresh_pack(manifest_path, roots=roots, knowledge_dir=out_dir)
    assert rebuilt_again is False
    assert info_again is not None
    assert info_again.pack_id == "findings"

    # 3. Modify source file: now is_stale is True, so it should rebuild
    fixture_corpus["doc"].write_text(
        "# Energy Transfer\n\n## Proximal Distal Sequence\n\nOur updated findings indicate altered energy transfer.\n",
        encoding="utf-8",
    )
    rebuilt_stale, info_stale = refresh_pack(manifest_path, roots=roots, knowledge_dir=out_dir)
    assert rebuilt_stale is True
    assert info_stale is not None

    # Search returns updated text
    pack_updated = KnowledgePack.open(pack_file)
    hits_updated = pack_updated.search("altered energy")
    assert len(hits_updated) >= 1
    assert "altered energy transfer" in hits_updated[0].text.lower()


def test_refresh_pack_missing_repo_skips_cleanly(fixture_corpus: dict[str, Path]) -> None:
    manifest_path = fixture_corpus["manifest"]
    roots = {"UpstreamDrift": fixture_corpus["repo"] / "nonexistent"}
    out_dir = fixture_corpus["out"]

    rebuilt, info = refresh_pack(manifest_path, roots=roots, knowledge_dir=out_dir)
    assert rebuilt is False
    assert info is None


def test_refresh_pack_corrupted_pack_rebuilds(fixture_corpus: dict[str, Path]) -> None:
    manifest_path = fixture_corpus["manifest"]
    roots = {"UpstreamDrift": fixture_corpus["repo"]}
    out_dir = fixture_corpus["out"]

    # Initial build
    rebuilt, info = refresh_pack(manifest_path, roots=roots, knowledge_dir=out_dir)
    assert rebuilt is True

    # Corrupt pack
    pack_file = out_dir / "findings.sqlite"
    pack_file.write_text("corrupted sqlite database", encoding="utf-8")

    # Refresh should recover by rebuilding
    rebuilt_recover, info_recover = refresh_pack(manifest_path, roots=roots, knowledge_dir=out_dir)
    assert rebuilt_recover is True
    assert info_recover is not None
    assert info_recover.pack_id == "findings"


def test_refresh_all_packs(fixture_corpus: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(fixture_corpus["repo"].parent))
    out_dir = fixture_corpus["out"]

    results = refresh_all_packs(rm_root=fixture_corpus["rm"], knowledge_dir=out_dir)
    assert len(results) == 1
    pack_id, rebuilt, info = results[0]
    assert pack_id == "findings"
    assert rebuilt is True
    assert info is not None
