"""API integration: Marge provenance is refused before shared memory persistence (#3936)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.api.test_memory_api import _XHR, _auth, hands_barb_memory_claim, memory_env, remote_client  # noqa: F401

pytestmark = pytest.mark.integration


def test_memory_append_refuses_marge_tag_before_storage(
    remote_client: TestClient, memory_env: dict[str, str], hands_barb_memory_claim: dict[str, str]
) -> None:
    res = remote_client.post(
        "/api/v1/memory",
        headers={**_XHR, **_auth(memory_env["hands"]), **hands_barb_memory_claim},
        json={
            "entry_type": "task",
            "source_product": "codex",
            "tags": ["+marge"],
            "body": {"title": "personal", "status": "open"},
        },
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "shared_memory_refused"
    assert "marge" in res.json()["detail"]["reason"].lower()

    listed = remote_client.get("/api/v1/memory?entry_type=task", headers=_auth(memory_env["hands"]))
    assert listed.status_code == 200
    assert listed.json()["entries"] == []
