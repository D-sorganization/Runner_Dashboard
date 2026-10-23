"""Fleet friction ledger + owner-questions queue (/api/v1/fleet/*)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, identity_manager

from scripts.import_friction_ledger import parse_ledger

pytestmark = pytest.mark.integration
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_REMOTE = ("100.64.0.25", 52002)
_FIXTURE = Path(__file__).parent / "fixtures" / "friction_ledger_sample.md"
_TOKENS = {
    "bot": ("svc_friction_bot_test", Principal(id="friction-bot", type="bot", name="Bot", roles=["bot", "fleet-peer"])),
    "other": (
        "svc_friction_other_test",
        Principal(id="friction-other", type="bot", name="Other", roles=["bot", "fleet-peer"]),
    ),
    "operator": ("svc_friction_op_test", Principal(id="friction-op", type="human", name="Op", roles=["operator"])),
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FLEET_FRICTION_DB", str(tmp_path / "friction.sqlite3"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(tmp_path / "identity"))
    for secret, principal in _TOKENS.values():
        identity_manager.add_ephemeral_token(hashlib.sha256(secret.encode()).hexdigest(), principal, name=principal.id)
    from server import app  # noqa: PLC0415

    yield TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _h(who: str) -> dict[str, str]:
    return {**_XHR, "Authorization": f"Bearer {_TOKENS[who][0]}"}


def _new(client: TestClient, **extra: object) -> dict:
    res = client.post("/api/v1/fleet/friction", headers=_h("bot"), json={"title": "t", "seen": "s", **extra})
    assert res.status_code == 201, res.text
    return res.json()


def test_create_requires_title_and_seen(client: TestClient) -> None:
    res = client.post("/api/v1/fleet/friction", headers=_h("bot"), json={"title": "only title"})
    assert res.status_code == 422


def test_create_assigns_f_id_and_creator(client: TestClient) -> None:
    row = _new(client, repo="Runner_Dashboard")
    assert row["id"] == "F1" and row["created_by"] == "friction-bot" and row["status"] == "open"
    assert _new(client)["id"] == "F2"


def test_close_without_detector_or_fix_ref_is_422(client: TestClient) -> None:
    fid = _new(client)["id"]
    for patch in ({"status": "closed"}, {"status": "closed", "fix_ref": "pr#1"}, {"status": "closed", "detector": "d"}):
        assert client.patch(f"/api/v1/fleet/friction/{fid}", headers=_h("bot"), json=patch).status_code == 422
    assert client.get("/api/v1/fleet/friction?status=closed", headers=_h("bot")).json()["count"] == 0
    ok = client.patch(
        f"/api/v1/fleet/friction/{fid}",
        headers=_h("bot"),
        json={"status": "closed", "fix_ref": "https://x/pr/1", "detector": "health brief check"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "closed"


def test_create_closed_without_detector_is_422(client: TestClient) -> None:
    res = client.post(
        "/api/v1/fleet/friction",
        headers=_h("bot"),
        json={"title": "t", "seen": "s", "status": "closed", "fix_ref": "x"},
    )
    assert res.status_code == 422


def test_recurrence_increments_and_stamps(client: TestClient) -> None:
    fid = _new(client)["id"]
    assert _new(client)["recurrence_count"] == 0
    first = client.post(f"/api/v1/fleet/friction/{fid}/recurrence", headers=_h("bot")).json()
    second = client.post(f"/api/v1/fleet/friction/{fid}/recurrence", headers=_h("bot")).json()
    assert (first["recurrence_count"], second["recurrence_count"]) == (1, 2)
    assert second["last_seen_at"]
    assert client.post("/api/v1/fleet/friction/F99/recurrence", headers=_h("bot")).status_code == 404


def test_list_filters_by_status_and_repo(client: TestClient) -> None:
    _new(client, repo="A")
    b = _new(client, repo="B")
    client.patch(f"/api/v1/fleet/friction/{b['id']}", headers=_h("bot"), json={"status": "waiting"})
    assert client.get("/api/v1/fleet/friction?repo=A", headers=_h("bot")).json()["count"] == 1
    assert client.get("/api/v1/fleet/friction?status=waiting", headers=_h("bot")).json()["items"][0]["id"] == b["id"]


def test_owner_question_answer_requires_operator(client: TestClient) -> None:
    q = client.post(
        "/api/v1/fleet/owner-questions",
        headers=_h("bot"),
        json={"question": "Merge?", "options": ["yes", "no"], "recommended": "yes", "repo": "R", "issue": 4},
    ).json()
    assert q["id"] == "Q1" and q["status"] == "open" and q["options"] == ["yes", "no"]
    url = f"/api/v1/fleet/owner-questions/{q['id']}/answer"
    assert client.post(url, headers=_h("bot"), json={"answer": "yes"}).status_code == 403
    done = client.post(url, headers=_h("operator"), json={"answer": "yes"})
    assert done.status_code == 200
    assert (done.json()["status"], done.json()["answered_by"]) == ("answered", "friction-op")
    assert client.post(url, headers=_h("operator"), json={"answer": "again"}).status_code == 409
    assert client.get("/api/v1/fleet/owner-questions?status=open", headers=_h("bot")).json()["count"] == 0


def test_withdraw_asker_or_operator_only(client: TestClient) -> None:
    ask = lambda: client.post("/api/v1/fleet/owner-questions", headers=_h("bot"), json={"question": "q"}).json()["id"]  # noqa: E731
    first, second = ask(), ask()
    assert client.post(f"/api/v1/fleet/owner-questions/{first}/withdraw", headers=_h("other")).status_code == 403
    assert (
        client.post(f"/api/v1/fleet/owner-questions/{first}/withdraw", headers=_h("bot")).json()["status"]
        == "withdrawn"
    )
    assert client.post(f"/api/v1/fleet/owner-questions/{second}/withdraw", headers=_h("operator")).status_code == 200


def test_summary_counts(client: TestClient, tmp_path: Path) -> None:
    from staff import friction_store  # noqa: PLC0415

    assert friction_store.open_counts() == {"friction_open": 0, "owner_questions_open": 0}
    _new(client)
    closed = _new(client)["id"]
    client.patch(
        f"/api/v1/fleet/friction/{closed}",
        headers=_h("bot"),
        json={"status": "closed", "fix_ref": "p", "detector": "d"},
    )
    client.post("/api/v1/fleet/owner-questions", headers=_h("bot"), json={"question": "q"})
    assert friction_store.open_counts() == {"friction_open": 1, "owner_questions_open": 1}


@pytest.mark.asyncio
async def test_staff_summary_includes_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import friction_store, summary_view  # noqa: PLC0415

    monkeypatch.setenv("FLEET_FRICTION_DB", str(tmp_path / "f.sqlite3"))
    friction_store.create_friction(title="t", seen="s", created_by="x")
    friction_store.create_question(question="q", asked_by="x")
    out = await summary_view.build_staff_summary()
    assert (out["friction_open"], out["owner_questions_open"]) == (1, 1)


def test_importer_parses_real_table_format() -> None:
    rows, warnings = parse_ledger(_FIXTURE.read_text(encoding="utf-8"))
    assert [r["ledger_id"] for r in rows] == ["F1", "F2", "F3"]
    f1, f2, f3 = rows
    assert f1["status"] == "closed" and f1["detector"].startswith("Guard refuses")
    assert f2["status"] == "open" and "#13109" in f2["fix_ref"] + f2["seen"]
    assert f3["status"] == "closed" and f3["fix_ref"].startswith("FRICTION_LEDGER.md#F3")
    assert all(r["title"] and len(r["title"]) <= 120 for r in rows)
    assert any("F3" in w for w in warnings)


def test_importer_dry_run_does_not_post(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.import_friction_ledger import main  # noqa: PLC0415

    assert main([str(_FIXTURE)]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
