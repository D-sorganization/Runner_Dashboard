"""Repository-scoped KnowledgePack search (Tools canonical search module)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from knowledge_pack.manifest import manifest_from_dict
from knowledge_pack.pack import KnowledgePack, build_pack
from knowledge_pack.search import search_repo

pytestmark = pytest.mark.unit


def _git_commit(root: Path, message: str) -> None:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@local"]
    subprocess.run(
        ["git", "-C", str(root), *identity, "add", "-A"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), *identity, "commit", "-qm", message],
        check=True,
    )


def _repo(root: Path, rel_path: str, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git_commit(root, "fixture")
    return root


def test_repo_filter_applies_before_bm25_top_k(tmp_path: Path) -> None:
    """A lower global BM25 rank still wins when the repo filter scopes results."""
    manifest = manifest_from_dict(
        {
            "id": "repo-filter",
            "sources": [
                {
                    "repo": "UpstreamDrift",
                    "authority": "findings",
                    "include": ["docs/**/*.md"],
                },
                {
                    "repo": "AffineDrift",
                    "authority": "findings",
                    "include": ["articles/**/*.md"],
                },
            ],
        }
    )
    corpus = {
        "UpstreamDrift": _repo(
            tmp_path / "UpstreamDrift",
            "docs/research/placeholder.md",
            "# Placeholder\n\nInitial corpus.\n",
        ),
        "AffineDrift": _repo(
            tmp_path / "AffineDrift",
            "articles/placeholder.md",
            "# Placeholder\n\nInitial corpus.\n",
        ),
    }
    target = corpus["UpstreamDrift"] / "docs/research/repo-filter.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Rare match\n\nThe pelvis timing is documented.\n", encoding="utf-8"
    )
    competitor = corpus["AffineDrift"] / "articles/pelvis.md"
    competitor.write_text("# Pelvis\n\nA competing pelvis result.\n", encoding="utf-8")
    _git_commit(corpus["UpstreamDrift"], "add repo-filter target")
    _git_commit(corpus["AffineDrift"], "add higher-ranked competitor")

    pack_path = tmp_path / "repo-filter.sqlite"
    build_pack(manifest, corpus, pack_path)
    pack = KnowledgePack.open(pack_path)

    assert pack.search("pelvis", k=1, hybrid=False)[0].repo == "AffineDrift"
    filtered = search_repo(pack, "pelvis", "UpstreamDrift", k=1, hybrid=False)
    assert [(hit.repo, hit.source) for hit in filtered] == [
        ("UpstreamDrift", "docs/research/repo-filter.md")
    ]
    assert search_repo(pack, "pelvis", "UpstreamDrift' OR 1=1 --") == []
