"""GET /api/v1/staff/audit pages the audit log (#1734).

The route called ``StaffAuditStore.query``, which does not exist (the store has
``list_entries``), so every admin read of the v1 audit log was a 500. The type
checker missed it because it never resolved backend imports.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import staff_v1
from staff.audit import StaffAuditRecord, StaffAuditStore

ADMIN = Principal(id="admin-test", type="human", name="Admin", roles=["admin"], scopes=["staff.audit.read"])


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store = StaffAuditStore(tmp_path / "audit.sqlite3")
    store.record(StaffAuditRecord(principal="operator:dieter", action="hold_set", target="holds"))
    store.record(StaffAuditRecord(principal="agent:barb", action="dispatch", target="night-watch"))
    monkeypatch.setattr(staff_v1, "get_audit_store", lambda: store)
    app = FastAPI()
    app.include_router(staff_v1.router)
    app.dependency_overrides[require_scope("staff.audit.read")] = lambda: ADMIN
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.unit
def test_v1_audit_lists_entries_newest_first(client: TestClient) -> None:
    res = client.get("/api/v1/staff/audit")

    assert res.status_code == 200, res.text
    assert [e["action"] for e in res.json()["entries"]] == ["dispatch", "hold_set"]


@pytest.mark.unit
def test_v1_audit_filters_by_action(client: TestClient) -> None:
    res = client.get("/api/v1/staff/audit", params={"action": "hold_set"})

    assert res.status_code == 200, res.text
    assert [e["principal"] for e in res.json()["entries"]] == ["operator:dieter"]
