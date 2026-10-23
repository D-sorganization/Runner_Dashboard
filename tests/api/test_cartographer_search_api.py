"""Canonical codebase search over actual Git commits and SQLite packs."""

import asyncio
import subprocess
import threading
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from knowledge_pack.cartographer import build_cartographer_pack
from knowledge_pack.pack import Passage
from routers import staff_knowledge

SearchFixture = tuple[TestClient, Path, Path]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SearchFixture:
    root = tmp_path / "UpstreamDrift"
    root.mkdir()
    (root / "SPEC.md").write_text("# Solver contracts\nEnergy contracts preserve timing. " + "界" * 900 + "\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    knowledge = tmp_path / "knowledge"
    build_cartographer_pack({"UpstreamDrift": root}, knowledge / "cartographer.sqlite")
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda repo: root if repo == "UpstreamDrift" else None)
    monkeypatch.setattr("dashboard_config.HOSTNAME", "desk")
    app = FastAPI()
    app.include_router(staff_knowledge.router, prefix="/api/v1/staff")
    reader = Principal(id="reader", type="human", name="Reader", roles=[], scopes=["staff.read"])
    app.dependency_overrides[require_scope("staff.read")] = lambda: reader
    return TestClient(app), root, knowledge


def test_search_returns_canonical_committed_citation_and_bounded_unicode(fixture: SearchFixture) -> None:
    client, root, _ = fixture
    response = client.get("/api/v1/staff/knowledge/cartographer/search", params={"q": "energy", "k": 3})
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == len(data["passages"]) and data["count"] > 0
    hit = data["passages"][0]
    commit = git(root, "rev-parse", "HEAD")
    assert hit["citation"].startswith(f"[ref:desk:codebase:UpstreamDrift@{commit[:8]}:SPEC.md#L")
    assert hit["commit"] == commit and len(hit["content_hash"]) == 64
    assert 0 < len(hit["text"].encode("utf-8")) <= 400
    assert data["stale"] is False


def test_commit_only_change_is_reported_stale_without_fabricating_new_reference(fixture: SearchFixture) -> None:
    client, root, _ = fixture
    old = git(root, "rev-parse", "HEAD")
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
    data = client.get("/api/v1/staff/knowledge/cartographer/search?q=energy").json()
    assert data["stale"] is True
    assert all(hit["commit"] == old for hit in data["passages"])
    assert client.get("/api/v1/staff/knowledge/cartographer").json()["stale"] is True


def test_uncommitted_indexed_text_is_omitted(fixture: SearchFixture) -> None:
    client, root, knowledge = fixture
    (root / "SPEC.md").write_text("# Contract\nDirtysecret energy schema\n")
    build_cartographer_pack({"UpstreamDrift": root}, knowledge / "cartographer.sqlite")
    data = client.get("/api/v1/staff/knowledge/cartographer/search?q=dirtysecret").json()
    assert data["count"] == 0 and data["passages"] == []


def test_missing_checkout_has_no_citations_and_does_not_claim_fresh(
    fixture: SearchFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = fixture
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _: None)
    data = client.get("/api/v1/staff/knowledge/cartographer/search?q=energy").json()
    assert data["count"] == 0 and data["stale"] is True


def test_codebase_search_still_requires_staff_read(fixture: SearchFixture) -> None:
    client, _, _ = fixture
    client.app.dependency_overrides.clear()
    client.app.dependency_overrides[require_principal] = lambda: Principal(
        id="unauthorized", type="bot", name="No read grant", roles=[], scopes=[]
    )
    assert client.get("/api/v1/staff/knowledge/cartographer/search?q=energy").status_code == 403


@pytest.mark.asyncio
async def test_slow_commit_verification_does_not_block_other_requests(
    fixture: SearchFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = fixture
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = staff_knowledge.format_codebase_reference

    def blocked(passage: Passage, root: Path) -> str | None:
        entered.set()
        try:
            assert release.wait(3), "test failed to release verification worker"
            return original(passage, root)
        finally:
            finished.set()

    @client.app.get("/health-probe")
    async def health() -> dict[str, bool]:
        return {"healthy": True}

    monkeypatch.setattr(staff_knowledge, "format_codebase_reference", blocked)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as http:
        task = asyncio.create_task(http.get("/api/v1/staff/knowledge/cartographer/search?q=energy"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await http.get("/health-probe")).status_code == 200
            assert not finished.is_set(), "other request waited for commit verification"
        finally:
            release.set()
            await task
