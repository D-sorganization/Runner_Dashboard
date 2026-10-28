"""API tests for expert panels (#1634, epic #1633).

- POST /api/v1/staff/panels creates a ``kind="panel"`` thread and runs the panel in the background.
- The cost guard reuses ``group_cost_guard_threshold_exceeded`` and is bypassed by ``confirm_cost``.
- GET /api/v1/staff/panels/{id} returns turns, stances, consensus and synthesis; 404 for other threads.
- Posting into a panel thread is a 409; a panel thread is never created through POST /threads.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import Principal, require_principal, require_scope
from routers import staff_panels
from server import app
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel import PanelSpeaker, TurnOutcome
from staff.panel_models import PanelCreateRequest
from staff.thread_bus import reset_thread_bus

TEST_OPERATOR = Principal(
    id="operator-user", type="human", name="Operator", roles=["operator"], scopes=["staff.chat", "staff.read"]
)
BODY: dict[str, Any] = {
    "topic": "Is a 1-D lumped model good enough for the heater?",
    "experts": [
        {"name": "Ada", "perspective": "numerical methods"},
        {"name": "Brook", "perspective": "experimental physics"},
        {"name": "Cy", "perspective": "operations"},
    ],
    "rounds": 2,
    "confirm_cost": True,
}


async def agreeing_runner(speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
    if speaker.name == "Moderator":
        return TurnOutcome(ok=True, text="**Consensus:** yes, with a validation run.")
    return TurnOutcome(ok=True, text=f"{speaker.name}: fine.\nSTANCE: agree\nPOSITION: good enough")


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setattr(staff_panels, "_turn_runner", lambda: agreeing_runner)
    reset_conversation_store()
    reset_thread_bus()
    app.dependency_overrides[require_principal] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.chat")] = lambda: TEST_OPERATOR
    app.dependency_overrides[require_scope("staff.read")] = lambda: TEST_OPERATOR
    yield
    app.dependency_overrides.clear()
    reset_conversation_store()
    reset_thread_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)


def _wait_done(client: TestClient, thread_id: str) -> dict[str, Any]:
    for _ in range(100):
        res = client.get(f"/api/v1/staff/panels/{thread_id}")
        assert res.status_code == 200, res.text
        if res.json()["status"] != "running":
            return res.json()
        time.sleep(0.05)
    raise AssertionError("panel never finished")


@pytest.mark.unit
def test_a_panel_runs_to_a_synthesis(client: TestClient) -> None:
    res = client.post("/api/v1/staff/panels", json=BODY)
    assert res.status_code == 202, res.text
    data = res.json()
    assert data["thread"]["kind"] == "panel"
    assert data["estimate"]["total_cost_usd"] > 0

    result = _wait_done(client, data["thread"]["id"])
    assert result["status"] == "complete" and result["consensus"] is True and result["rounds_used"] == 1
    assert [t["expert"] for t in result["turns"]] == ["Ada", "Brook", "Cy"]
    assert result["synthesis"].startswith("**Consensus:**")


def test_api_accepts_preload_options_and_returns_persisted_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from staff import panel

    seen = []

    def preload(request: PanelCreateRequest) -> str:
        seen.append(request)
        return "## Codebase & Architecture Context (Cited)\nReference fixture"

    monkeypatch.setattr(panel, "_preload_codebase_context", preload)
    response = client.post("/api/v1/staff/panels", json={**BODY, "repos": ["AffineDrift"], "preload_codebase": True})
    assert response.status_code == 202
    assert seen[0].repos == ["AffineDrift"] and seen[0].pack_id == "cartographer"
    assert response.json()["thread"]["meta"]["panel"]["codebase_context"].endswith("Reference fixture")


@pytest.mark.unit
def test_the_cost_guard_needs_confirmation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUP_COST_GUARD_THRESHOLD_USD", "0.0001")
    res = client.post("/api/v1/staff/panels", json={**BODY, "confirm_cost": False})
    assert res.status_code == 400
    err = res.json()
    detail = err.get("error") or err.get("detail") or {}  # the app's error envelope renames "detail"
    assert detail["code"] == "group_cost_guard_threshold_exceeded" and "confirm_cost" in detail["message"]
    assert get_conversation_store().list_threads() == []
    assert client.post("/api/v1/staff/panels", json=BODY).status_code == 202


@pytest.mark.unit
@pytest.mark.parametrize(
    "patch",
    [{"experts": BODY["experts"][:2]}, {"rounds": 9}, {"topic": ""}, {"mode": "shouting"}, {"extra": 1}],
)
def test_bad_panels_are_422(client: TestClient, patch: dict[str, Any]) -> None:
    assert client.post("/api/v1/staff/panels", json={**BODY, **patch}).status_code == 422


@pytest.mark.unit
def test_too_many_running_panels_is_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staff_panels, "active_panel_count", lambda: 2)
    assert client.post("/api/v1/staff/panels", json=BODY).status_code == 429


@pytest.mark.unit
def test_presets_list(client: TestClient) -> None:
    res = client.get("/api/v1/staff/panels/presets")
    assert res.status_code == 200
    presets = res.json()["presets"]
    assert presets and all(3 <= len(p["experts"]) <= 4 for p in presets)
    assert "claude" in res.json()["providers"]


@pytest.mark.unit
def test_presets_leave_out_providers_switched_off_on_this_node(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "gemini")
    providers = client.get("/api/v1/staff/panels/presets").json()["providers"]
    assert "gemini" not in providers
    assert "claude" in providers


def _gemini_expert() -> dict[str, Any]:
    return {**BODY, "experts": [{**BODY["experts"][0], "provider": "gemini"}, *BODY["experts"][1:]]}


def _gemini_moderator() -> dict[str, Any]:
    return {**BODY, "moderator_provider": "gemini"}


@pytest.mark.unit
@pytest.mark.parametrize("make_body", [_gemini_expert, _gemini_moderator], ids=["expert", "moderator"])
def test_a_seat_on_a_disabled_provider_is_422_like_an_unknown_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, make_body: Any
) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "gemini")
    res = client.post("/api/v1/staff/panels", json=make_body())
    assert res.status_code == 422, res.text
    assert "disabled on this node" in res.text
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS")
    assert client.post("/api/v1/staff/panels", json=make_body()).status_code == 202


@pytest.mark.unit
def test_a_non_panel_thread_is_404(client: TestClient) -> None:
    thread = get_conversation_store().create_thread(title="chat", kind="direct")
    assert client.get(f"/api/v1/staff/panels/{thread.id}").status_code == 404
    assert client.get("/api/v1/staff/panels/th_missing").status_code == 404


@pytest.mark.unit
def test_posting_into_a_panel_is_409_and_panels_are_not_made_through_threads(client: TestClient) -> None:
    thread_id = client.post("/api/v1/staff/panels", json=BODY).json()["thread"]["id"]
    _wait_done(client, thread_id)
    res = client.post(
        f"/api/v1/staff/threads/{thread_id}/messages",
        json={"body": "one more thing"},
        headers={"Idempotency-Key": "k-1"},
    )
    assert res.status_code == 409 and "panel" in res.text
    assert client.post("/api/v1/staff/threads", json={"kind": "panel"}).status_code == 422


@pytest.mark.unit
@pytest.mark.parametrize("model", ["--dangerously-skip-permissions", "x; rm -rf /", "a b"])
def test_a_hostile_seat_model_is_422_naming_the_seat(client: TestClient, model: str) -> None:
    experts = [{**BODY["experts"][0], "model": model}, *BODY["experts"][1:]]
    res = client.post("/api/v1/staff/panels", json={**BODY, "experts": experts})
    assert res.status_code == 422, res.text
    assert "Ada" in res.text and get_conversation_store().list_threads() == []


@pytest.mark.unit
def test_the_panel_state_exposes_tally_and_quorum(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def flaky(speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        if speaker.name == "Cy":
            return TurnOutcome(ok=False, error="boom")
        return await agreeing_runner(speaker, prompt, thread_id, message_id)

    monkeypatch.setattr(staff_panels, "_turn_runner", lambda: flaky)
    res = client.post("/api/v1/staff/panels", json=BODY)
    result = _wait_done(client, res.json()["thread"]["id"])
    assert result["tally"] == {"participated": 2, "abstained": 1}
    assert result["abstentions"] == [{"expert": "Cy", "reason": "boom"}]
    assert result["quorum_met"] is True and result["status"] == "complete"


def _presets_by_id(client: TestClient) -> dict[str, Any]:
    return {p["id"]: p for p in client.get("/api/v1/staff/panels/presets").json()["presets"]}


@pytest.mark.unit
def test_fleet_decision_preset_pins_a_provider_and_model_per_seat(client: TestClient) -> None:
    presets = _presets_by_id(client)
    seats = {e["name"]: e for e in presets["fleet-decision"]["experts"]}
    assert presets["fleet-decision"]["title"] == "Fleet decision panel (Argon / Astra / Fable)"
    assert (seats["Argon"]["provider"], seats["Argon"]["model"]) == ("antigravity", "gemini-4-argon-high")
    assert (seats["Astra"]["provider"], seats["Astra"]["model"]) == ("codex", "gpt-6-astra")
    assert (seats["Fable"]["provider"], seats["Fable"]["model"]) == ("claude", "claude-fable-5-1")
    assert all(e["available"] for e in seats.values())
    # Older presets stay valid: provider defaults to claude, model is null.
    assert presets["science"]["experts"][0]["provider"] == "claude"
    assert presets["science"]["experts"][0]["model"] is None


@pytest.mark.unit
def test_a_disabled_provider_marks_its_seat_unavailable_without_swapping_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "antigravity")
    seats = {e["name"]: e for e in _presets_by_id(client)["fleet-decision"]["experts"]}
    assert seats["Argon"]["available"] is False
    assert seats["Argon"]["provider"] == "antigravity"  # visible, not silently replaced
    assert seats["Astra"]["available"] is True and seats["Fable"]["available"] is True


@pytest.mark.unit
def test_the_fleet_decision_experts_are_a_valid_panel_request(client: TestClient) -> None:
    keys = ("name", "perspective", "provider", "model")
    experts = [{k: e[k] for k in keys} for e in _presets_by_id(client)["fleet-decision"]["experts"]]
    res = client.post("/api/v1/staff/panels", json={**BODY, "experts": experts})
    assert res.status_code == 202, res.text
