from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from library.db import init_library_db
from library.sources import add_source
from routers import library as library_routes


def _app(tmp_path: Path, monkeypatch) -> tuple[FastAPI, str, str]:
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "HOSTNAME", "desk")
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {})
    db = init_library_db(tmp_path / "library.sqlite")
    shared_root, private_root = tmp_path / "shared", tmp_path / "private"
    shared_root.mkdir()
    private_root.mkdir()
    add_source(db, source_id="shared", label="Shared", root_path=shared_root, shared=True)
    add_source(db, source_id="private", label="Private", root_path=private_root, shared=False)
    with sqlite3.connect(db) as conn:
        for work_id, source_id, root, digest, title, text in (
            (1, "shared", shared_root, "b" * 64, "Shared work", "Rotational power improves turbine efficiency."),
            (2, "private", private_root, "e" * 64, "Private work", "Rotational power improves turbine efficiency."),
        ):
            conn.execute(
                "INSERT INTO works(id,sha256,title,format,status) VALUES(?,?,?,'txt','active')",
                (work_id, "a" * 63 + str(work_id), title),
            )
            conn.execute(
                "INSERT INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
                "VALUES(?,?,?,0,20,?,'present',?)",
                (work_id, source_id, f"{source_id}.txt", digest, str(root)),
            )
            conn.execute(
                "INSERT INTO passages(id,work_id,page_start,page_end,text,token_estimate) VALUES(?,?,4,5,?,6)",
                (work_id + 6, work_id, text),
            )
        conn.commit()
    library_routes.set_db_path(db)
    app = FastAPI()
    app.include_router(library_routes.router)
    app.dependency_overrides[library_routes.require_library_peer] = lambda: Principal(
        id="operator", type="human", name="Operator", scopes=["fleet.read"]
    )
    shared_cite = "[ref:desk:shared:w1:p7#sha256=bbbbbbbb:p.4-5]"
    private_cite = "[ref:desk:private:w2:p8#sha256=eeeeeeee:p.4-5]"
    return app, shared_cite, private_cite


def test_verify_shared_only_hides_private_source_but_default_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    app, shared_cite, private_cite = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    shared_claim = f'"Rotational power improves turbine efficiency" {shared_cite}'
    private_claim = f'"Rotational power improves turbine efficiency" {private_cite}'

    shared = client.post("/api/v1/library/verify", json={"content": shared_claim, "shared_only": True})
    hidden_private = client.post("/api/v1/library/verify", json={"content": private_claim, "shared_only": True})
    legacy_default = client.post("/api/v1/library/verify", json={"content": private_claim})
    explicit_default = client.post("/api/v1/library/verify", json={"content": private_claim, "shared_only": False})

    assert shared.status_code == 200 and shared.json()["verified"]
    assert shared.json()["shared_only"] is True
    assert hidden_private.status_code == 200
    assert hidden_private.json()["verified"] is False
    assert hidden_private.json()["citations"][0]["status"] == "citation_not_found"
    assert "Private work" not in hidden_private.text
    assert legacy_default.status_code == 200 and legacy_default.json()["verified"]
    assert "shared_only" not in legacy_default.json()
    assert explicit_default.status_code == 200 and explicit_default.json()["verified"]
    assert "shared_only" not in explicit_default.json()


def test_verify_shared_only_requires_literal_boolean(tmp_path: Path, monkeypatch) -> None:
    app, _, _ = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    for value in (None, "true", 1, [], {}):
        response = client.post(
            "/api/v1/library/verify",
            json={"content": "ordinary text", "shared_only": value},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "shared_only must be a boolean"


def test_shared_only_rejects_repository_link_context_before_checkout_lookup(tmp_path: Path, monkeypatch) -> None:
    app, _, _ = _app(tmp_path, monkeypatch)
    client = TestClient(app)

    def fail_lookup(_repo: str | None) -> None:
        raise AssertionError("shared-only verification must not resolve a checkout")

    monkeypatch.setattr("routers.library_verify.verification_repo_root", fail_lookup)
    response = client.post(
        "/api/v1/library/verify",
        json={"content": "[guide](docs/guide.md)", "repo": "Runner_Dashboard", "shared_only": True},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "repo verification is unavailable in shared_only mode"


def test_shared_only_rejects_codebase_and_links_without_filesystem_io(tmp_path: Path, monkeypatch) -> None:
    from library import markdown_links, verify

    db = init_library_db(tmp_path / "library.sqlite")
    codebase_ref = "[ref:desk:codebase:Runner_Dashboard@12345678:docs/private.md#L1-L2]"
    content = f"{codebase_ref}\nSee [private](docs/private.md) and [outside](https://example.invalid)."

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unsupported shared-only context must not touch files")

    monkeypatch.setattr(verify, "verify_codebase_citations", unexpected)
    monkeypatch.setattr(markdown_links, "verify_links", unexpected)
    monkeypatch.setattr(verify, "verify_links", unexpected)
    result = verify.verify_content(db, content, repo_root=tmp_path, shared_only=True)

    assert result["verified"] is False
    assert result["citations"] == [
        {"citation": codebase_ref, "verified": False, "status": "unsupported_in_shared_only"}
    ]
    assert [link["status"] for link in result["links"]] == [
        "unsupported_in_shared_only",
        "unsupported_in_shared_only",
    ]
    assert all(link["verified"] is False for link in result["links"])


def test_shared_only_propagates_and_peer_filters_private_sources(tmp_path: Path, monkeypatch) -> None:
    import dashboard_config
    from library import verify

    peer_app, peer_shared_cite, peer_private_cite = _app(tmp_path / "peer", monkeypatch)
    peer_shared_cite = peer_shared_cite.replace("desk:", "peer:")
    peer_private_cite = peer_private_cite.replace("desk:", "peer:")
    db = init_library_db(tmp_path / "hub.sqlite")
    monkeypatch.setattr(dashboard_config, "HOSTNAME", "hub")
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {"peer": "https://peer.tailnet:8321"})
    monkeypatch.setenv("HUB_FLEET_TOKEN", "unit-test")
    seen: list[dict[str, object]] = []
    peer_client = TestClient(peer_app)

    class ResponseContext:
        def __init__(self, body: bytes):
            self.body = body
            self.status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            yield self.body

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            seen.append({"method": method, "url": url, **kwargs})
            dashboard_config.HOSTNAME = "peer"
            try:
                peer_response = peer_client.post(url, json=kwargs["json"], headers=kwargs["headers"])
            finally:
                dashboard_config.HOSTNAME = "hub"
            seen[-1]["peer_status"] = peer_response.status_code
            seen[-1]["peer_body"] = peer_response.text
            return ResponseContext(peer_response.content)

    monkeypatch.setattr(verify.httpx, "AsyncClient", FakeClient)
    claim = '"Rotational power improves turbine efficiency" '
    private = verify.verify_content(db, claim + peer_private_cite, shared_only=True)
    shared = verify.verify_content(db, claim + peer_shared_cite, shared_only=True)

    assert private["verified"] is False
    assert seen[0]["peer_status"] == 200
    assert json.loads(seen[0]["peer_body"])["citations"][0]["status"] == "citation_not_found"
    assert "Private work" not in seen[0]["peer_body"]
    assert private["citations"][0]["status"] == "citation_not_found"
    assert shared["verified"] is True
    assert len(seen) == 2
    assert all(call["url"] == "https://peer.tailnet:8321/api/v1/library/verify" for call in seen)
    assert all(call["headers"] == {"Authorization": "Bearer unit-test"} for call in seen)
    assert all(call["json"]["shared_only"] is True and call["json"]["local_only"] is True for call in seen)


@pytest.mark.parametrize("ack", [None, False])
def test_shared_only_rejects_peer_without_shared_mode_ack(tmp_path: Path, monkeypatch, ack: bool | None) -> None:
    import dashboard_config
    from library import verify

    db = init_library_db(tmp_path / "library.sqlite")
    monkeypatch.setattr(dashboard_config, "HOSTNAME", "hub")
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {"peer": "https://peer.tailnet:8321"})
    monkeypatch.setenv("HUB_FLEET_TOKEN", "unit-test")
    citation = "[ref:peer:shared:w1:p7#sha256=bbbbbbbb:p.4-5]"
    response_body: dict[str, object] = {
        "verified": True,
        "citations": [
            {
                "citation": citation,
                "node": "peer",
                "verified": True,
                "status": "verified",
                "work": {"id": 1, "title": "Private work"},
                "file": {"source_id": "shared", "sha256": "b" * 64},
                "passage": {"id": 7, "page_start": 4, "page_end": 5},
            }
        ],
    }
    if ack is not None:
        response_body["shared_only"] = ack

    class ResponseContext:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            yield json.dumps(response_body).encode()

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return ResponseContext()

    monkeypatch.setattr(verify.httpx, "AsyncClient", FakeClient)
    result = verify.verify_content(db, citation, shared_only=True)

    assert result["verified"] is False
    assert result["citations"][0]["status"] == "peer_unavailable"
    assert "work" not in result["citations"][0]
    assert "file" not in result["citations"][0]


def test_peer_receiver_honors_shared_only_for_private_citation(tmp_path: Path, monkeypatch) -> None:
    app, _, private_cite = _app(tmp_path, monkeypatch)
    app.dependency_overrides[library_routes.require_library_peer] = lambda: Principal(
        id="fleet-peer", type="bot", name="Peer", roles=["fleet-peer"]
    )
    response = TestClient(app).post(
        "/api/v1/library/verify",
        json={"content": private_cite, "shared_only": True, "local_only": True},
    )
    assert response.status_code == 200
    assert response.json()["verified"] is False
    assert response.json()["citations"][0]["status"] == "citation_not_found"
    assert "Private work" not in response.text


def test_verify_content_rejects_non_boolean_shared_only(tmp_path: Path) -> None:
    from library.verify import verify_content

    db = init_library_db(tmp_path / "library.sqlite")
    for value in ("true", 1, None):
        with pytest.raises(ValueError, match="shared_only must be a boolean"):
            verify_content(db, "text", shared_only=value)
