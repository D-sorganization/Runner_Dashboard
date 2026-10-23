"""Exercise the actual refresh service and role chat against committed source packs."""

import subprocess
from pathlib import Path

import pytest
import yaml
from knowledge_pack import KnowledgePack, build_pack, load_manifest
from staff.chat_knowledge import build_knowledge_turn_block
from staff.knowledge_refresh import refresh_pack
from staff.roles import RoleSpec

pytestmark = pytest.mark.unit

REPOS = ("UpstreamDrift", "AffineDrift", "Gasification_Model", "Tools", "Repository_Management", "Runner_Dashboard")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def source_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    roots = {}
    for repo in REPOS:
        root = tmp_path / "repos" / repo
        root.mkdir(parents=True)
        (root / "SPEC.md").write_text(f"# {repo} architecture\nSolver contracts preserve energy.\n")
        (root / "private-unlisted.md").write_text("Private material must not enter the codebase map.\n")
        git(root, "init", "-q")
        git(root, "add", ".")
        git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        roots[repo] = root
    manifest = tmp_path / "cartographer.yml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "id": "cartographer",
                "title": "Codebase map",
                "sources": [{"repo": repo, "include": ["*.md"], "authority": "published"} for repo in REPOS],
            }
        )
    )
    knowledge = tmp_path / "knowledge"
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path / "repos"))
    return roots, manifest, knowledge


def test_refresh_uses_fixed_sources_and_tracks_commit_only_changes(source_pack) -> None:
    roots, manifest, knowledge = source_pack
    build_pack(load_manifest(manifest), roots, knowledge / "cartographer.sqlite")
    assert KnowledgePack.open(knowledge / "cartographer.sqlite").search("Private material")
    rebuilt, info = refresh_pack(manifest, roots=roots, knowledge_dir=knowledge)
    assert rebuilt and info is not None and info.files == 6
    pack = KnowledgePack.open(knowledge / "cartographer.sqlite")
    assert pack.search("Private material") == []
    assert refresh_pack(manifest, roots=roots, knowledge_dir=knowledge)[0] is False
    root = roots["UpstreamDrift"]
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "new head",
    )
    rebuilt, info = refresh_pack(manifest, roots=roots, knowledge_dir=knowledge)
    assert rebuilt and info.commits["UpstreamDrift"] == git(root, "rev-parse", "HEAD")


def test_role_chat_returns_reproducible_codebase_reference(source_pack, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dashboard_config.HOSTNAME", "fleet-runner-" + "x" * 50)
    roots, manifest, knowledge = source_pack
    refresh_pack(manifest, roots=roots, knowledge_dir=knowledge)
    role = RoleSpec(
        name="cartographer", title="Cartographer", tools=["search_knowledge"], scope={"pack": "cartographer"}
    )
    block = build_knowledge_turn_block(role, "solver contracts")
    assert block and block.startswith("## Knowledge")
    assert ":codebase:" in block and ":SPEC.md#L" in block and "[ref:" in block
    assert "Private material" not in block
    assert all(f"@{git(roots[repo], 'rev-parse', 'HEAD')[:8]}:" in block for repo in REPOS if repo in block)
    for repo in REPOS:
        targeted = build_knowledge_turn_block(role, f"{repo} solver contracts")
        assert f":codebase:{repo}@{git(roots[repo], 'rev-parse', 'HEAD')[:8]}:" in targeted


def test_dirty_text_is_not_given_a_committed_citation(source_pack) -> None:
    roots, manifest, knowledge = source_pack
    (roots["UpstreamDrift"] / "SPEC.md").write_text("# Architecture\nUncommitted secretphrase schema.\n")
    refresh_pack(manifest, roots=roots, knowledge_dir=knowledge, force=True)
    role = RoleSpec(
        name="cartographer", title="Cartographer", tools=["search_knowledge"], scope={"pack": "cartographer"}
    )
    block = build_knowledge_turn_block(role, "secretphrase")
    assert block and "secretphrase" not in block


def test_search_code_supplies_scoped_indexed_context_without_rebuilding(source_pack, monkeypatch) -> None:
    roots, manifest, knowledge = source_pack
    refresh_pack(manifest, roots=roots, knowledge_dir=knowledge)
    before = (knowledge / "cartographer.sqlite").read_bytes()
    monkeypatch.setattr("knowledge_pack.cartographer.build_cartographer_pack", lambda *_: pytest.fail("cold rebuild"))
    monkeypatch.setattr("knowledge_pack.pack.select_files", lambda *_: pytest.fail("checkout enumeration"))
    role = RoleSpec(name="scout", title="Scout", repos=("UpstreamDrift",), chat={"tools": ["search_code"]})
    block = build_knowledge_turn_block(role, "solver contracts")
    assert block and "## Code map search" in block and ":codebase:UpstreamDrift@" in block
    assert "AffineDrift" not in block and len(block.encode()) <= 800
    assert (knowledge / "cartographer.sqlite").read_bytes() == before


def test_search_code_missing_pack_is_explicit_and_never_scans(source_pack, monkeypatch) -> None:
    monkeypatch.setattr("knowledge_pack.cartographer.build_cartographer_pack", lambda *_: pytest.fail("cold rebuild"))
    role = RoleSpec(name="scout", title="Scout", chat={"tools": ["search_code"]})
    assert "not available" in (build_knowledge_turn_block(role, "solver contracts") or "")


def test_search_code_omits_uncommitted_text_and_preserves_other_knowledge(source_pack) -> None:
    roots, manifest, knowledge = source_pack
    (roots["UpstreamDrift"] / "SPEC.md").write_text("# Map\nUncommitted secretphrase contract.\n")
    refresh_pack(manifest, roots=roots, knowledge_dir=knowledge, force=True)
    role = RoleSpec(
        name="scout",
        title="Scout",
        repos=("UpstreamDrift",),
        chat={"tools": ["search_code", "search_knowledge"]},
        scope={"pack": "findings"},
    )
    block = build_knowledge_turn_block(role, "secretphrase")
    assert block and "secretphrase" not in block and "No verifiable indexed" in block
    assert "Knowledge pack 'findings' is not available" in block


def test_search_code_filters_legacy_broad_sources_and_reports_head_staleness(source_pack) -> None:
    roots, manifest, knowledge = source_pack
    build_pack(load_manifest(manifest), roots, knowledge / "cartographer.sqlite")
    role = RoleSpec(name="scout", title="Scout", chat={"tools": ["search_code"]})
    block = build_knowledge_turn_block(role, "Private material")
    assert block and "Private material" not in block and "No verifiable indexed" in block
    git(
        roots["UpstreamDrift"],
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "new head",
    )
    block = build_knowledge_turn_block(role, "UpstreamDrift solver")
    assert block and "Indexed code map is stale" in block and "[ref:" in block
