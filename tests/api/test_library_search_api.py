from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from library.ingest import ingest_directory
from routers import library as library_routes

from tests.library.conftest import make_text_pdf

READER = Principal(id="r", type="user", name="Reader", roles=["viewer"], scopes=["fleet.read"])
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _app(tmp_path: Path, monkeypatch) -> FastAPI:
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "HOSTNAME", "desk", raising=False)
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {}, raising=False)
    cfg = tmp_path / "collections.yml"
    cfg.write_text("repos: {}\ntopics:\n  Golf:\n    folders: [Golf]\n")
    monkeypatch.setenv("LIBRARY_COLLECTIONS_PATH", str(cfg))
    root = tmp_path / "src"
    (root / "Golf").mkdir(parents=True)
    (root / "Golf" / "Jones - Swing Science.pdf").write_bytes(make_text_pdf("hip joint torque golf swing"))
    db = tmp_path / "library.sqlite"
    ingest_directory(root, db_path=db)
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return app


def test_library_search(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))
    # Operator-local search can read the default private ingest source.
    resp = client.get(
        "/api/v1/library/search", params={"q": "hip joint torque golf", "budget": 1500, "scope": "local"}, headers=_XHR
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokens_used"] <= 1500 and body["passages"][0]["file_path"]
    assert client.get("/api/v1/library/stats", headers=_XHR).status_code == 200
    wid = body["passages"][0]["work_id"]
    assert client.get(f"/api/v1/library/works/{wid}", headers=_XHR).json()["topics"] == ["Golf"]
    assert client.get("/api/v1/library/search", params={"q": "", "topic": "Golf"}, headers=_XHR).status_code == 422
    assert client.get("/api/v1/library/search", params={"q": "golf", "topic": "nope"}, headers=_XHR).status_code == 422


def test_library_verify_endpoints(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))
    cite = "[ref:desk:shared:w1:p1#sha256=00000000:p.1-1]"

    response = client.post("/api/v1/library/verify", json={"content": cite}, headers=_XHR)

    assert response.status_code == 200
    assert response.json()["citations"][0]["status"] == "citation_not_found"
    local_response = client.post("/api/v1/library/verify", json={"content": cite, "local_only": True}, headers=_XHR)
    assert local_response.status_code == 200
    assert local_response.json()["citations"][0]["status"] == "citation_not_found"
    remote_local_only = client.post(
        "/api/v1/library/verify",
        json={"content": cite.replace("desk", "peer"), "local_only": True},
        headers=_XHR,
    )
    assert remote_local_only.status_code == 200
    assert remote_local_only.json()["citations"][0]["status"] == "node_mismatch"
    assert (
        client.post("/api/v1/library/verify", json={"content": cite, "local_only": "yes"}, headers=_XHR).status_code
        == 422
    )
    assert client.get("/api/v1/library/verify", params={"cite": "not-a-citation"}, headers=_XHR).status_code == 422
    assert client.post("/api/v1/library/verify", json={"content": 123}, headers=_XHR).status_code == 422


def test_verify_links_use_only_the_explicit_trusted_checkout(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))
    repos = tmp_path / "repos"
    checkout = repos / "Runner_Dashboard"
    docs = checkout / "docs"
    docs.mkdir(parents=True)
    (checkout / ".git").mkdir()
    (docs / "guide.md").write_text("# Getting Started\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Secret\n", encoding="utf-8")
    (docs / "escape.md").symlink_to(outside)
    escaped_checkout = tmp_path / "escaped-checkout"
    (escaped_checkout / ".git").mkdir(parents=True)
    (repos / "Escaped").symlink_to(escaped_checkout, target_is_directory=True)
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(repos))
    content = "See [the guide](docs/guide.md#getting-started)."

    response = client.post(
        "/api/v1/library/verify", json={"content": content, "repo": "Runner_Dashboard"}, headers=_XHR
    )
    no_context = client.post("/api/v1/library/verify", json={"content": content}, headers=_XHR)
    unknown_repo = client.post("/api/v1/library/verify", json={"content": content, "repo": "missing"}, headers=_XHR)
    escaped_repo = client.post("/api/v1/library/verify", json={"content": content, "repo": "Escaped"}, headers=_XHR)
    bad_name = client.post("/api/v1/library/verify", json={"content": content, "repo": "../outside"}, headers=_XHR)
    traversal = client.post(
        "/api/v1/library/verify",
        json={"content": "[escape](../../outside.md)", "repo": "Runner_Dashboard"},
        headers=_XHR,
    )
    absolute = client.post(
        "/api/v1/library/verify", json={"content": f"[escape]({outside})", "repo": "Runner_Dashboard"}, headers=_XHR
    )
    symlink = client.post(
        "/api/v1/library/verify", json={"content": "[escape](docs/escape.md)", "repo": "Runner_Dashboard"}, headers=_XHR
    )

    assert response.status_code == 200, response.text
    assert response.json()["links"][0]["verified"] is True
    assert no_context.json()["links"][0]["status"] == "broken_link"
    assert unknown_repo.status_code == 404
    assert escaped_repo.status_code == 404
    assert bad_name.status_code == 422
    assert traversal.json()["links"][0]["status"] == "broken_link"
    assert absolute.json()["links"][0]["status"] == "broken_link"
    assert symlink.json()["links"][0]["status"] == "broken_link"


def test_verify_authorizes_run_before_resolving_repo(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))

    def deny_run(*_args, **_kwargs) -> None:
        raise PermissionError("not run owner")

    def fail_repo_lookup(_repo: str | None) -> None:
        raise AssertionError("repository lookup preceded run authorization")

    monkeypatch.setattr("library.retrieval_ledger.authorize_run_owner", deny_run)
    monkeypatch.setattr("routers.library_verify.verification_repo_root", fail_repo_lookup)
    response = client.post(
        "/api/v1/library/verify",
        json={"content": "[relative](docs/file.md)", "run_id": "foreign-run", "repo": "Runner_Dashboard"},
        headers=_XHR,
    )

    assert response.status_code == 403
