"""``PUT /api/agent-remediation/config`` saves the tuned policy.

The route called ``agent_remediation._load_workflow_type_rules`` and
``agent_remediation._as_tuple_strings``; the package ``__init__`` never exported
them, so every save from the Remediation settings form was a 500. Found by
resolving cross-module imports in mypy (#1734).
"""

from __future__ import annotations

from typing import Any

import agent_remediation
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import remediation

OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["remediation.dispatch"])


@pytest.mark.unit
def test_put_config_saves_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[Any] = []
    monkeypatch.setattr(agent_remediation, "save_policy", saved.append)
    monkeypatch.setattr(agent_remediation, "probe_provider_availability", dict)
    app = FastAPI()
    app.include_router(remediation.router)
    app.dependency_overrides[require_scope("remediation.dispatch")] = lambda: OPERATOR
    client = TestClient(app, raise_server_exceptions=False)

    res = client.put("/api/agent-remediation/config", json={"policy": {"provider_order": ["codex", "claude"]}})

    assert res.status_code == 200, res.text
    assert saved[0].provider_order == ("codex", "claude")
    assert res.json()["policy"]["provider_order"] == ["codex", "claude"]
