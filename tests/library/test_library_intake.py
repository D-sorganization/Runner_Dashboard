"""Tests for persistent content-addressed chat attachments & Librarian Intake Queue (#2541)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from library.db import init_library_db
from library.intake import format_attachment_for_prompt
from routers import library as library_router


@pytest.fixture()
def intake_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    db_path = tmp_path / "library.sqlite"
    init_library_db(db_path)
    library_router.set_db_path(db_path)
    monkeypatch.setenv("HUB_FLEET_TOKEN", "test-intake-token")
    app = FastAPI()
    app.include_router(library_router.router)
    return TestClient(app), db_path


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_intake_defaults_to_confidential_work_and_deduplicates_cas(
    intake_client: tuple[TestClient, Path],
) -> None:
    client, db_path = intake_client
    headers = {"Authorization": "Bearer test-intake-token"}
    body = " Navier-Stokes boundary layer notes for UpstreamDrift reference. "
    res = client.post(
        "/api/v1/library/intake",
        headers=headers,
        json={"filename": "boundary-layer.md", "content_base64": _b64(body), "thread_id": "th_demo123"},
    )
    assert res.status_code == 200, res.text
    item = res.json()
    assert item["filename"] == "boundary-layer.md"
    assert item["sensitivity"] == "confidential_work"
    assert item["status"] == "staged"
    assert len(item["sha256"]) == 64
    cas_blob = db_path.parent / "chat_attachments" / item["sha256"][:2] / item["sha256"]
    assert cas_blob.read_text(encoding="utf-8") == body
    listed = client.get("/api/v1/library/intake", headers=headers).json()
    assert listed["count"] == 1
    assert "storage_path" not in listed["items"][0]


def test_pre_disk_secret_classification_quarantines_and_blocks_promotion(
    intake_client: tuple[TestClient, Path],
) -> None:
    client, db_path = intake_client
    headers = {"Authorization": "Bearer test-intake-token"}
    canary_secret = "sk-proj-CANARY9876543210SECRETKEYVALUE"
    res = client.post(
        "/api/v1/library/intake",
        headers=headers,
        json={
            "filename": "production-notes.txt",
            "content_base64": _b64(f"OPENAI_API_KEY={canary_secret}\n"),
            "sensitivity": "public_reference",
        },
    )
    assert res.status_code == 200, res.text
    item = res.json()
    assert item["sensitivity"] == "vault_sensitive"
    assert item["status"] == "quarantined_sensitive"
    assert item["sha256"].startswith("hmac256:")
    assert canary_secret not in res.text
    cas_root = db_path.parent / "chat_attachments"
    assert not cas_root.exists() or list(cas_root.rglob("*")) == []
    promote_res = client.post(f"/api/v1/library/intake/{item['id']}/promote", headers=headers)
    assert promote_res.status_code == 403
    assert canary_secret not in promote_res.text
    prompt_stub = format_attachment_for_prompt(item)
    assert "vault_sensitive attachment redacted" in prompt_stub
    assert canary_secret not in prompt_stub


def test_explicit_promotion_indexes_work_and_searchable_passages(
    intake_client: tuple[TestClient, Path],
) -> None:
    client, _db_path = intake_client
    headers = {"Authorization": "Bearer test-intake-token"}
    doc_text = " syngas shift equilibrium kinetic rate constant verification for reactor modeling "
    stage_res = client.post(
        "/api/v1/library/intake",
        headers=headers,
        json={"filename": "syngas-kinetics.md", "content_base64": _b64(doc_text), "sensitivity": "public_reference"},
    )
    staged = stage_res.json()
    pre = client.get(
        "/api/v1/library/search", params={"q": "syngas shift equilibrium", "scope": "local"}, headers=headers
    )
    assert pre.json()["passages"] == []
    prom_res = client.post(f"/api/v1/library/intake/{staged['id']}/promote", headers=headers)
    assert prom_res.status_code == 200, prom_res.text
    promoted = prom_res.json()
    assert promoted["status"] == "promoted"
    post = client.get(
        "/api/v1/library/search", params={"q": "syngas shift equilibrium", "scope": "local"}, headers=headers
    )
    assert post.json()["passages"][0]["work_id"] == promoted["work_id"]
