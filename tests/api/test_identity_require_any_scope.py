"""require_any_scope dependency (Barb PR hygiene auth)."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from identity import Principal, principal_has_scope, require_any_scope

app = FastAPI()

_any_hygiene = require_any_scope("staff.admin", "staff.write")


@app.get("/x")
async def _x(caller: Principal = Depends(_any_hygiene)) -> dict[str, str]:
    return {"id": caller.id}


def test_require_any_scope_accepts_matching_principal() -> None:
    prin = Principal(id="agent-grok", type="bot", name="G", roles=["bot", "barb"])
    assert principal_has_scope(prin, "staff.write")
    app.dependency_overrides[_any_hygiene] = lambda: prin
    try:
        r = TestClient(app).get("/x")
        assert r.status_code == 200
        assert r.json()["id"] == "agent-grok"
    finally:
        app.dependency_overrides.clear()
