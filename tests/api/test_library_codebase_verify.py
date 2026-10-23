"""Potentially blocking Git verification belongs off the ASGI event loop."""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from library.db import init_library_db
from routers import library, library_verify


def test_post_verification_does_not_run_in_event_loop(monkeypatch, tmp_path) -> None:
    app = FastAPI()
    app.include_router(library.router)
    app.dependency_overrides[library.require_library_peer] = lambda: Principal(id="owner", type="human", name="Owner")
    db = init_library_db(tmp_path / "library.sqlite")
    monkeypatch.setattr(library, "_db", lambda: db)

    def verify(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return {"verified": True, "citations": [], "links": []}
        return {"verified": False, "citations": [], "links": []}

    monkeypatch.setattr(library_verify, "verify_content", verify)
    response = TestClient(app).post("/api/v1/library/verify", json={"content": "source claim"})
    assert response.status_code == 200 and response.json()["verified"], response.text
