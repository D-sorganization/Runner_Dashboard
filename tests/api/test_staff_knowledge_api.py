"""API tests for staff knowledge pack endpoints (Issue #1479)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from identity import Principal, require_principal, require_scope  # noqa: E402
from knowledge_pack import build_pack, load_manifest  # noqa: E402
from server import app  # noqa: E402

TEST_PRINCIPAL = Principal(
    id="operator-alice",
    type="human",
    name="Alice",
    roles=["operator"],
    scopes=["staff.read", "staff.chat"],
)


@pytest.fixture(autouse=True)
def override_auth() -> Any:
    app.dependency_overrides[require_principal] = lambda: TEST_PRINCIPAL
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_PRINCIPAL
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(
        app,
        headers={"X-Requested-With": "XMLHttpRequest"},
        raise_server_exceptions=False,
    )


@pytest.fixture
def test_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Build a test knowledge pack and configure STAFF_KNOWLEDGE_DIR."""
    repo_dir = tmp_path / "Repositories" / "AffineDrift"
    docs_dir = repo_dir / "articles"
    docs_dir.mkdir(parents=True, exist_ok=True)
    article = docs_dir / "timing.md"
    article.write_text(
        "# Proximal Distal Timing\n\n"
        "## Energy Sequencing\n\n"
        "Proximal to distal kinetic chain sequencing drives ball velocity.\n",
        encoding="utf-8",
    )
    (repo_dir / ".git").mkdir()

    manifest_file = tmp_path / "findings.yml"
    manifest_file.write_text(
        "id: test_findings\n"
        "title: Test Findings Pack\n"
        "chunk_chars: 1800\n"
        "sources:\n"
        "  - repo: AffineDrift\n"
        "    authority: published\n"
        "    include:\n"
        "      - 'articles/**/*.md'\n",
        encoding="utf-8",
    )

    pack_dir = tmp_path / "knowledge_dir"
    pack_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(pack_dir))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path / "Repositories"))

    manifest = load_manifest(manifest_file)
    build_pack(manifest, {"AffineDrift": repo_dir}, pack_dir / "test_findings.sqlite")

    return "test_findings"


def test_get_knowledge_pack_info_404_when_missing(client: TestClient) -> None:
    resp = client.get("/api/v1/staff/knowledge/nonexistent_pack")
    assert resp.status_code == 404
    data = resp.json()
    err_msg = data.get("error", {}).get("message") or data.get("detail", "")
    assert "not found" in err_msg.lower()


def test_get_knowledge_pack_info_success(client: TestClient, test_pack: str) -> None:
    resp = client.get(f"/api/v1/staff/knowledge/{test_pack}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pack_id"] == "test_findings"
    assert data["title"] == "Test Findings Pack"
    assert "built_at" in data
    assert "commits" in data
    assert "stale" in data
    assert data["passages"] >= 1
    assert data["passage_count"] == data["passages"]


def test_get_knowledge_pack_search_success(client: TestClient, test_pack: str) -> None:
    resp = client.get(f"/api/v1/staff/knowledge/{test_pack}/search", params={"q": "kinetic chain", "k": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["pack_id"] == "test_findings"
    assert data["query"] == "kinetic chain"
    assert data["count"] >= 1
    hit = data["passages"][0]
    assert "kinetic chain" in hit["text"].lower()
    assert hit["citation"].startswith("AffineDrift:articles/timing.md")


def test_get_knowledge_pack_search_404_when_missing(client: TestClient) -> None:
    resp = client.get("/api/v1/staff/knowledge/missing_pack/search", params={"q": "anything"})
    assert resp.status_code == 404
