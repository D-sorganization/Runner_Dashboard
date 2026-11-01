"""Tests for the staff-threads-API relay endpoint (#3705 follow-up).

``POST /threads/{thread_id}/relay`` is the HTTP surface for ``rd_barb_notify.persona_relay``:
an RD persona role forwards one request verbatim to its Grok Bot counterpart via Barb, gated by
an operator-configured per-role allowlist, and audited through ``staff.audit.record_audit``.
``persona_relay`` itself (validation, allowlist contract, delivery, dedupe) is exercised directly
in ``tests/unit/test_rd_barb_notify.py``; these tests cover only the router's wiring: it reaches
``persona_relay`` with the right arguments and maps its result to the right HTTP status and audit
row, following the same ``monkeypatch.setattr(router, "persona_notify", ...)`` precedent as
``tests/api/test_staff_role_model_override.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_fleet_peer, require_scope
from routers import staff_threads
from routers.staff_threads import router as staff_threads_router
from staff.audit import StaffAuditStore, reset_audit_store
from staff.conversations import ConversationStore

PEER_PRINCIPAL = Principal(
    id="fleet-peer", type="peer", name="Node B", roles=["fleet-peer"], scopes=["staff.read", "staff.chat"]
)


@pytest.fixture
def relay_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    reset_audit_store()
    audit_db = tmp_path / "audit.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(audit_db))

    conv_store = ConversationStore(tmp_path / "conv.sqlite3")
    thread = conv_store.create_thread(title="Relay", kind="direct", role="pm-gasification", created_by="user:dieter")

    app = FastAPI()
    app.include_router(staff_threads_router, prefix="/api/v1/staff")
    app.dependency_overrides[require_fleet_peer] = lambda: "fleet-peer"
    app.dependency_overrides[require_scope("staff.read")] = lambda: PEER_PRINCIPAL
    app.dependency_overrides[require_scope("staff.chat")] = lambda: PEER_PRINCIPAL
    staff_threads._get_store_or_503 = lambda: conv_store

    env = {"client": TestClient(app), "thread": thread, "audit_db": audit_db}
    yield env
    reset_audit_store()


@pytest.mark.unit
def test_relay_allowed_is_sent_and_audited(relay_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_persona_relay(from_role, to_grok_bot, request, thread_id, *, allowed_pairs, audit):  # noqa: ANN001
        calls.append(
            {
                "from_role": from_role,
                "to_grok_bot": to_grok_bot,
                "request": request,
                "thread_id": thread_id,
                "allowed_pairs": allowed_pairs,
            }
        )
        result = {"ok": True, "status": "sent"}
        audit({"from_role": from_role, "to_grok_bot": to_grok_bot, "result": result})
        return result

    monkeypatch.setattr(staff_threads, "persona_relay", fake_persona_relay)
    monkeypatch.setattr(
        staff_threads, "relay_allowlist_from_config", lambda cfg, role: {"pm-gasification": "pm-gasification"}
    )

    client: TestClient = relay_env["client"]
    thread_id = relay_env["thread"].id

    resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/relay",
        json={"from_role": "pm-gasification", "to_grok_bot": "pm-gasification", "request": "status of #5586?"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "status": "sent"}
    assert calls[0]["thread_id"] == thread_id
    assert calls[0]["allowed_pairs"] == {"pm-gasification": "pm-gasification"}

    store = StaffAuditStore(relay_env["audit_db"])
    entries = store.list_entries(thread_id=thread_id)
    assert len(entries) == 1
    assert entries[0].action == "relay_to_grok_bot"
    assert entries[0].outcome == "sent"
    assert entries[0].target == "grok:pm-gasification"
    assert entries[0].principal == "role:pm-gasification"


@pytest.mark.unit
def test_relay_denied_returns_403_and_is_audited(relay_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_persona_relay(from_role, to_grok_bot, request, thread_id, *, allowed_pairs, audit):  # noqa: ANN001
        result = {"ok": False, "status": "denied", "error": "relay_not_allowed", "problems": ["not on allowlist"]}
        audit({"from_role": from_role, "to_grok_bot": to_grok_bot, "result": result})
        return result

    monkeypatch.setattr(staff_threads, "persona_relay", fake_persona_relay)
    monkeypatch.setattr(staff_threads, "relay_allowlist_from_config", lambda cfg, role: {})

    client: TestClient = relay_env["client"]
    thread_id = relay_env["thread"].id

    resp = client.post(
        f"/api/v1/staff/threads/{thread_id}/relay",
        json={"from_role": "pm-gasification", "to_grok_bot": "night-watch", "request": "status?"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "relay_not_allowed"

    store = StaffAuditStore(relay_env["audit_db"])
    entries = store.list_entries(thread_id=thread_id)
    assert len(entries) == 1
    assert entries[0].outcome == "denied"


@pytest.mark.unit
def test_relay_uses_the_from_role_to_load_its_own_allowlist(
    relay_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint scopes config lookup to ``body.from_role``, never a hardcoded role (#3705)."""
    seen_roles: list[str] = []

    monkeypatch.setattr(staff_threads, "load_config", lambda role: {"role": role})

    def fake_allowlist(cfg, role):  # noqa: ANN001
        seen_roles.append(role)
        return {}

    monkeypatch.setattr(staff_threads, "relay_allowlist_from_config", fake_allowlist)
    monkeypatch.setattr(
        staff_threads,
        "persona_relay",
        lambda *a, **kw: {"ok": False, "status": "denied", "error": "relay_not_allowed", "problems": []},
    )

    client: TestClient = relay_env["client"]
    thread_id = relay_env["thread"].id
    client.post(
        f"/api/v1/staff/threads/{thread_id}/relay",
        json={"from_role": "karl", "to_grok_bot": "karl", "request": "status?"},
    )
    assert seen_roles == ["karl"]
