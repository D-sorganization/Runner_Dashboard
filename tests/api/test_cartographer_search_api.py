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
from library.codebase_retrieval_ledger import codebase_references_for_run
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


def test_search_records_only_emitted_canonical_references_for_authenticated_run_owner(
    fixture: SearchFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root, _ = fixture
    db = tmp_path / "library.sqlite"
    monkeypatch.setattr("library.db.library_db_path", lambda: db)

    class Runs:
        @staticmethod
        def get_run(run_id: str) -> object:
            assert run_id == "run-123"
            return type("Run", (), {"requested_by": "reader"})()

    monkeypatch.setattr("staff.store.get_run_store", lambda: Runs())
    response = client.get("/api/v1/staff/knowledge/cartographer/search", params={"q": "energy", "run_id": "run-123"})
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-123"
    assert data["count"] == len(data["passages"]) > 0
    rows = codebase_references_for_run(db, "run-123")
    assert len(rows) == len(data["passages"])
    for row, passage in zip(rows, sorted(data["passages"], key=lambda hit: hit["citation"]), strict=True):
        assert row["citation"] == passage["citation"]
        assert row["commit_sha"] == git(root, "rev-parse", "HEAD")
        assert row["content_hash"] == passage["content_hash"]
        assert row["relative_path"] == "SPEC.md"
        assert row["line_start"] > 0 and row["line_end"] >= row["line_start"]
        assert "text" not in row
    repeated = client.get("/api/v1/staff/knowledge/cartographer/search", params={"q": "energy", "run_id": "run-123"})
    assert repeated.status_code == 200
    assert len(codebase_references_for_run(db, "run-123")) == len(rows)


def test_run_scoped_empty_search_records_no_reference(
    fixture: SearchFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = fixture
    db = tmp_path / "library.sqlite"
    monkeypatch.setattr("library.db.library_db_path", lambda: db)

    class Runs:
        @staticmethod
        def get_run(_run_id: str) -> object:
            return type("Run", (), {"requested_by": "reader"})()

    monkeypatch.setattr("staff.store.get_run_store", lambda: Runs())
    response = client.get(
        "/api/v1/staff/knowledge/cartographer/search", params={"q": "not-in-index", "run_id": "run-123"}
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert response.json()["run_id"] == "run-123"
    assert codebase_references_for_run(db, "run-123") == []


@pytest.mark.parametrize(
    ("run", "status"),
    [(None, 404), (type("Run", (), {"requested_by": "someone-else"})(), 403)],
)
def test_run_scoped_search_requires_existing_owned_run(
    fixture: SearchFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run: object,
    status: int,
) -> None:
    client, _, _ = fixture
    db = tmp_path / "library.sqlite"
    monkeypatch.setattr("library.db.library_db_path", lambda: db)

    class Runs:
        @staticmethod
        def get_run(_run_id: str) -> object:
            return run

    monkeypatch.setattr("staff.store.get_run_store", lambda: Runs())
    response = client.get("/api/v1/staff/knowledge/cartographer/search", params={"q": "energy", "run_id": "run-123"})
    assert response.status_code == status
    assert codebase_references_for_run(db, "run-123") == []


def test_run_id_is_rejected_for_non_cartographer_search(fixture: SearchFixture) -> None:
    client, _, _ = fixture
    assert client.get("/api/v1/staff/knowledge/other/search?q=energy&run_id=run-123").status_code == 422


def test_run_scoped_search_fails_closed_if_cited_passage_cannot_be_recorded(
    fixture: SearchFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = fixture
    db = tmp_path / "library.sqlite"
    monkeypatch.setattr("library.db.library_db_path", lambda: db)

    class Runs:
        @staticmethod
        def get_run(_run_id: str) -> object:
            return type("Run", (), {"requested_by": "reader"})()

    monkeypatch.setattr("staff.store.get_run_store", lambda: Runs())
    monkeypatch.setattr(
        staff_knowledge,
        "_search_codebase",
        lambda *_args: {
            "pack_id": "cartographer",
            "query": "energy",
            "count": 1,
            "passages": [
                {
                    "repo": "UpstreamDrift",
                    "source": "SPEC.md",
                    "anchor": "contracts",
                    "title": "Contracts",
                    "text": "text",
                    "commit": "1" * 40,
                    "content_hash": "2" * 64,
                    "status": "current",
                    "authority": "published",
                    "score": 1.0,
                    "citation": "forged",
                }
            ],
            "stale": False,
        },
    )
    response = client.get("/api/v1/staff/knowledge/cartographer/search", params={"q": "energy", "run_id": "run-123"})
    assert response.status_code == 500
    assert "forged" not in response.text
    assert codebase_references_for_run(db, "run-123") == []
