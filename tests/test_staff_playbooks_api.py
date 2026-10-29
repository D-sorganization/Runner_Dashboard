"""Tests for the playbooks list/render/assign API (#3488)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

MANUAL_YAML = """\
schema_version: 1
version: "1.0.0"
title: Barb Operating Manual
sections:
  - anchor: claim-before-touching
    title: Claim Before Touching
    body: Every worker must acquire an exclusive issue lease via POST /claims before pushing or arming.
"""

PLAYBOOK_YAML = """\
schema_version: 1
playbook_id: overnight-codex-swarm
version: "1.2.0"
title: Overnight Codex Swarm Playbook
summary: Run bounded overnight swarm lanes following manual anchors.
manual_anchors:
  - "#claim-before-touching"
directive_refs:
  - fleet-standards
role_refs:
  - barb
  - codex-worker
parameters:
  - name: max_lanes
    param_type: integer
    required: true
    default: 4
  - name: mode
    param_type: enum
    required: false
    choices: [standard, drain]
  - name: lane_id
    param_type: string
    required: true
steps:
  - "Follow manual#claim-before-touching."
"""


@pytest.fixture
def playbooks_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "manual.yml").write_text(MANUAL_YAML)
    (tmp_path / "overnight-codex-swarm.yml").write_text(PLAYBOOK_YAML)
    monkeypatch.setenv("STAFF_PLAYBOOKS_DIR", str(tmp_path))
    import staff.playbooks.store as store_mod

    store_mod.clear_playbooks_cache()
    yield tmp_path
    store_mod.clear_playbooks_cache()


@pytest.fixture
def client(playbooks_dir: Path) -> Any:
    import staff.playbooks.assignments as assignments_mod
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer
    from server import app

    assignments_mod.clear_assignments()
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)
        assignments_mod.clear_assignments()


def test_list_playbooks_returns_pinned_summaries(client: Any) -> None:
    resp = client.get("/api/v1/staff/playbooks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["playbooks"]) == 1
    entry = body["playbooks"][0]
    assert entry["playbook_id"] == "overnight-codex-swarm"
    assert entry["pin"].startswith("overnight-codex-swarm@1.2.0+")


def test_render_playbook_resolves_parameters_and_sets_etag(client: Any) -> None:
    resp = client.get("/api/v1/staff/playbooks/overnight-codex-swarm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["playbook_id"] == "overnight-codex-swarm"
    params = {p["name"]: p for p in body["parameters"]}
    assert params["max_lanes"]["default"] == 4
    assert resp.headers["ETag"]


def test_render_playbook_returns_304_when_etag_matches(client: Any) -> None:
    first = client.get("/api/v1/staff/playbooks/overnight-codex-swarm")
    etag = first.headers["ETag"]
    second = client.get("/api/v1/staff/playbooks/overnight-codex-swarm", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_render_unknown_playbook_is_404(client: Any) -> None:
    resp = client.get("/api/v1/staff/playbooks/does-not-exist")
    assert resp.status_code == 404


def test_assign_with_eligible_role_and_valid_params_succeeds(client: Any) -> None:
    resp = client.post(
        "/api/v1/staff/playbooks/overnight-codex-swarm/assign",
        json={"role": "codex-worker", "parameters": {"max_lanes": 6, "mode": "drain", "lane_id": "codex-d"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "ASSIGNED"
    assert body["parameters"] == {"max_lanes": 6, "mode": "drain", "lane_id": "codex-d"}
    assert body["pin"].startswith("overnight-codex-swarm@1.2.0+")


def test_assign_applies_parameter_defaults(client: Any) -> None:
    resp = client.post(
        "/api/v1/staff/playbooks/overnight-codex-swarm/assign",
        json={"role": "barb", "parameters": {"lane_id": "claude-a"}},
    )
    assert resp.status_code == 200
    assert resp.json()["parameters"]["max_lanes"] == 4


def test_assign_with_unauthorized_role_fails_closed(client: Any) -> None:
    import staff.playbooks.assignments as assignments_mod

    resp = client.post(
        "/api/v1/staff/playbooks/overnight-codex-swarm/assign",
        json={"role": "agy-c", "parameters": {"max_lanes": 2, "lane_id": "agy-c"}},
    )
    assert resp.status_code == 403
    assert assignments_mod.list_assignments() == []


def test_assign_with_bad_parameter_choice_fails_closed(client: Any) -> None:
    import staff.playbooks.assignments as assignments_mod

    resp = client.post(
        "/api/v1/staff/playbooks/overnight-codex-swarm/assign",
        json={"role": "barb", "parameters": {"mode": "not-a-choice", "lane_id": "claude-a"}},
    )
    assert resp.status_code == 400
    assert assignments_mod.list_assignments() == []


def test_assign_with_missing_required_parameter_fails_closed(client: Any) -> None:
    import staff.playbooks.assignments as assignments_mod

    resp = client.post(
        "/api/v1/staff/playbooks/overnight-codex-swarm/assign",
        json={"role": "barb", "parameters": {"max_lanes": 4}},
    )
    assert resp.status_code == 400
    assert assignments_mod.list_assignments() == []


def test_assign_unknown_playbook_is_404_and_writes_nothing(client: Any) -> None:
    import staff.playbooks.assignments as assignments_mod

    resp = client.post(
        "/api/v1/staff/playbooks/does-not-exist/assign",
        json={"role": "barb", "parameters": {}},
    )
    assert resp.status_code == 404
    assert assignments_mod.list_assignments() == []


def _assign(client: Any) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/staff/playbooks/overnight-codex-swarm/assign",
        json={"role": "codex-worker", "parameters": {"max_lanes": 4, "lane_id": "codex-d"}},
    )
    assert resp.status_code == 200
    return resp.json()


def test_fetch_assignment_returns_current_state(client: Any) -> None:
    created = _assign(client)
    resp = client.get(f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}")
    assert resp.status_code == 200 and resp.json()["state"] == "ASSIGNED"


def test_fetch_unknown_assignment_is_404(client: Any) -> None:
    resp = client.get("/api/v1/staff/playbooks/assignments/999999")
    assert resp.status_code == 404


def test_ack_stamps_lane_and_version_hash(client: Any) -> None:
    created = _assign(client)
    resp = client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/ack",
        json={"lane_id": "codex-d", "version_hash": created["pin"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "ACKNOWLEDGED" and body["lane_id"] == "codex-d"
    assert body["events"][-1]["version_hash"] == created["pin"]


def test_ack_with_stale_version_hash_fails_closed(client: Any) -> None:
    created = _assign(client)
    resp = client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/ack",
        json={"lane_id": "codex-d", "version_hash": "stale-pin"},
    )
    assert resp.status_code == 400


def test_complete_requires_prior_ack_and_stamps_version_hash(client: Any) -> None:
    created = _assign(client)
    client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/ack",
        json={"lane_id": "codex-d", "version_hash": created["pin"]},
    )
    resp = client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/complete",
        json={"lane_id": "codex-d", "version_hash": created["pin"], "outcome": "COMPLETED"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "COMPLETED" and body["events"][-1]["version_hash"] == created["pin"]


def test_complete_by_a_different_lane_is_forbidden(client: Any) -> None:
    created = _assign(client)
    client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/ack",
        json={"lane_id": "codex-d", "version_hash": created["pin"]},
    )
    resp = client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/complete",
        json={"lane_id": "claude-a", "version_hash": created["pin"], "outcome": "COMPLETED"},
    )
    assert resp.status_code == 403


def test_complete_with_invalid_outcome_is_400(client: Any) -> None:
    created = _assign(client)
    client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/ack",
        json={"lane_id": "codex-d", "version_hash": created["pin"]},
    )
    resp = client.post(
        f"/api/v1/staff/playbooks/assignments/{created['assignment_id']}/complete",
        json={"lane_id": "codex-d", "version_hash": created["pin"], "outcome": "DONE"},
    )
    assert resp.status_code == 400
