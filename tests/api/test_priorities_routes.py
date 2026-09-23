"""HTTP surface and auth for /api/priorities (issue #1227, epic #1192).

Reads need a fleet peer once ``HUB_FLEET_TOKEN`` is set; the PUT needs a
principal holding ``coordination.write`` (``bot`` / ``operator`` presets) or a
loopback orchestrator peer, plus the CSRF header.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import identity
import pytest
from fastapi.testclient import TestClient
from identity import SCOPE_PRESETS, Principal

FIXTURE = Path(__file__).parent / "fixtures" / "board_meeting" / "consensus.md.txt"
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.9", 51000)
_LOOPBACK = ("127.0.0.1", 51000)
_TOKENS = {
    "bot-tok": Principal(id="bot-1", type="bot", name="Bot", roles=["bot"]),
    "viewer-tok": Principal(id="viewer-1", type="human", name="Viewer", roles=["viewer"]),
}
_BODY = {"directives": [{"text": "Finish the coordination API", "priority": 1, "repo": "Runner_Dashboard"}]}


@pytest.fixture
def rm_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "Repository_Management"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check_agent_claim.py").write_text("", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "fleet_manifest.yaml").write_text(
        "portfolios:\n  infra:\n    description: Plumbing.\n    wip_limit: 6\n    repos: [Runner_Dashboard]\n",
        encoding="utf-8",
    )
    meeting = root / "docs" / "board-meetings" / "2026-09-21"
    meeting.mkdir(parents=True)
    shutil.copy(FIXTURE, meeting / "consensus.md")
    (meeting / "instructions.md").write_text("# do the thing", encoding="utf-8")
    monkeypatch.setenv("STAFF_RM_ROOT", str(root))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: _TOKENS.get(raw))
    return root


def _client(monkeypatch: pytest.MonkeyPatch, peer: tuple[str, int], loopback_auth: str) -> Iterator[TestClient]:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", loopback_auth)
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=peer)


@pytest.fixture
def remote(rm_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, _REMOTE, "0")


@pytest.fixture
def loopback(rm_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, _LOOPBACK, "1")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
def test_scope_presets_grant_coordination_write() -> None:
    assert "coordination.write" in SCOPE_PRESETS["bot"]
    assert "coordination.write" in SCOPE_PRESETS["operator"]
    assert "coordination.write" not in SCOPE_PRESETS["viewer"]


@pytest.mark.unit
def test_reads_require_fleet_peer(remote: TestClient) -> None:
    for path in ("/api/priorities", "/api/priorities/meetings", "/api/priorities/directives"):
        assert remote.get(path).status_code == 401, path
        assert remote.get(path, headers=_bearer("fleet-secret-token")).status_code == 200, path


@pytest.mark.unit
def test_priorities_snapshot_shape(remote: TestClient) -> None:
    body = remote.get("/api/priorities", headers=_bearer("fleet-secret-token")).json()
    assert body["available"] is True
    assert body["board"]["date"] == "2026-09-21"
    assert body["board"]["active"][0]["item"] == "Fleet coordination API"
    assert body["portfolios"][0]["name"] == "infra"
    assert "generated_at" in body


@pytest.mark.unit
def test_meetings_list_and_detail(remote: TestClient) -> None:
    listing = remote.get("/api/priorities/meetings", headers=_bearer("fleet-secret-token")).json()
    assert listing["meetings"][0]["date"] == "2026-09-21"
    detail = remote.get("/api/priorities/meetings/2026-09-21", headers=_bearer("fleet-secret-token")).json()
    assert detail["instructions"] == "# do the thing"
    assert detail["consensus"]["borda"][0]["score"] == 14


@pytest.mark.unit
def test_meeting_detail_validates_date_and_404s(remote: TestClient) -> None:
    headers = _bearer("fleet-secret-token")
    assert remote.get("/api/priorities/meetings/2026-01-01", headers=headers).status_code == 404
    assert remote.get("/api/priorities/meetings/latest", headers=headers).status_code == 422
    assert remote.get("/api/priorities/meetings/2026-02-30", headers=headers).status_code == 422


@pytest.mark.unit
def test_put_directives_with_bot_scope(remote: TestClient) -> None:
    resp = remote.put("/api/priorities/directives", json=_BODY, headers={**_XHR, **_bearer("bot-tok")})
    assert resp.status_code == 200, resp.text
    saved = resp.json()["directives"]
    assert saved[0]["set_by"] == "principal:bot-1"
    got = remote.get("/api/priorities/directives", headers=_bearer("fleet-secret-token")).json()
    assert [d["text"] for d in got["directives"]] == ["Finish the coordination API"]


@pytest.mark.unit
def test_put_directives_rejects_missing_scope_and_fleet_token(remote: TestClient) -> None:
    viewer = remote.put("/api/priorities/directives", json=_BODY, headers={**_XHR, **_bearer("viewer-tok")})
    assert viewer.status_code == 403
    fleet = remote.put("/api/priorities/directives", json=_BODY, headers={**_XHR, **_bearer("fleet-secret-token")})
    assert fleet.status_code == 401
    anon = remote.put("/api/priorities/directives", json=_BODY, headers=_XHR)
    assert anon.status_code == 401


@pytest.mark.unit
def test_put_directives_requires_csrf_header(loopback: TestClient) -> None:
    assert loopback.put("/api/priorities/directives", json=_BODY).status_code == 403
    ok = loopback.put("/api/priorities/directives", json=_BODY, headers=_XHR)
    assert ok.status_code == 200, ok.text


@pytest.mark.unit
def test_put_directives_validation_is_422(loopback: TestClient) -> None:
    bad = {"directives": [{"text": "x", "priority": 9}]}
    assert loopback.put("/api/priorities/directives", json=bad, headers=_XHR).status_code == 422
    dup = {"directives": [{"text": "same"}, {"text": "same"}]}
    assert loopback.put("/api/priorities/directives", json=dup, headers=_XHR).status_code == 422
