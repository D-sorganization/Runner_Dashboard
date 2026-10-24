"""Dispatch exclusions: store, routes, dispatch enforcement, scheduler skip, summary count."""

# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import staff_exclusions
from staff import exclusions as excl_mod
from staff.exclusions import ExclusionsList

from tests.api.test_staff_fleet import client, staff  # noqa: F401,F811  (shared /run fixtures)
from tests.api.test_staff_schedule import _la, sched  # noqa: F401,F811

_XHR = {"X-Requested-With": "XMLHttpRequest"}
OPERATOR = Principal(
    id="operator-test", type="human", name="Op", roles=["operator"], scopes=["staff.read", "staff.holds.write"]
)


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def _exclusions_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "excl.json"
    monkeypatch.setenv("STAFF_EXCLUSIONS_FILE", str(path))
    return path


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(staff_exclusions, "record_audit", lambda **kw: 0)
    app = FastAPI()
    app.include_router(staff_exclusions.router, prefix="/api/staff")
    app.dependency_overrides[require_scope("staff.read")] = lambda: OPERATOR
    app.dependency_overrides[require_scope("staff.holds.write")] = lambda: OPERATOR
    with TestClient(app) as c:
        yield c


# --- store -----------------------------------------------------------------


@pytest.mark.unit
def test_matching_rules() -> None:
    lst = ExclusionsList()
    lst.add({"repo": "D-sorganization/Tools", "issue": 7, "owner": "codex", "reason": "lane"})
    assert lst.blocking("Tools", 7) is not None
    assert lst.blocking("tools", 7) is not None  # case + org prefix insensitive
    assert lst.blocking("Tools", 8) is None
    assert lst.blocking("Other", 7) is None
    assert lst.blocking("Tools", None) is None


@pytest.mark.unit
def test_whole_repo_exclusion_blocks_every_issue() -> None:
    lst = ExclusionsList()
    lst.add({"repo": "Tools", "owner": "peer", "reason": "refactor"})
    assert lst.blocking("Tools", 1) is not None
    assert lst.blocking("Tools", None) is not None
    assert lst.blocking("Gasification", 1) is None


@pytest.mark.unit
def test_expired_exclusion_does_not_block() -> None:
    lst = ExclusionsList()
    lst.add({"repo": "Tools", "owner": "p", "reason": "r", "expires_at": _iso(timedelta(hours=-1))})
    assert lst.blocking("Tools", 1) is None
    assert lst.active() == []
    lst.add({"repo": "Tools", "issue": 2, "owner": "p", "reason": "r", "expires_at": _iso(timedelta(hours=1))})
    assert lst.blocking("Tools", 2) is not None


@pytest.mark.unit
def test_path_glob_needs_a_declared_path() -> None:
    lst = ExclusionsList()
    lst.add({"repo": "Tools", "owner": "p", "reason": "r", "path_glob": "src/movement/*"})
    assert lst.blocking("Tools", 1, ["src/movement/opt.py"]) is not None
    assert lst.blocking("Tools", 1, ["docs/a.md"]) is None
    assert lst.blocking("Tools", 1, []) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    [
        {"repo": "", "owner": "o", "reason": "r"},
        {"repo": "T", "owner": "", "reason": "r"},
        {"repo": "T", "owner": "o", "reason": ""},
        {"repo": "T", "owner": "o", "reason": "r", "issue": 0},
        {"repo": "T", "owner": "o", "reason": "r", "expires_at": "tomorrow-ish"},
    ],
)
def test_invalid_entries_rejected(data: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ExclusionsList().add(data)


@pytest.mark.unit
def test_readd_same_lane_replaces_and_unreadable_file_is_empty(_exclusions_file: Path) -> None:
    lst = ExclusionsList()
    lst.add({"repo": "T", "issue": 1, "owner": "a", "reason": "r1"})
    lst.add({"repo": "T", "issue": 1, "owner": "b", "reason": "r2"})
    assert [e.owner for e in lst.load()] == ["b"]
    _exclusions_file.write_text("{not json", encoding="utf-8")
    assert lst.load() == []


# --- routes ----------------------------------------------------------------


@pytest.mark.unit
def test_post_get_delete_roundtrip(api: TestClient) -> None:
    res = api.post("/api/staff/exclusions", json={"repo": "Tools", "issue": 5, "owner": "codex", "reason": "lane"})
    assert res.status_code == 201, res.text
    entry = res.json()["exclusion"]
    assert entry["created_by"] and entry["created_at"] and entry["issue"] == 5
    listed = api.get("/api/staff/exclusions").json()["exclusions"]
    assert [e["id"] for e in listed] == [entry["id"]] and listed[0]["active"] is True
    assert api.delete(f"/api/staff/exclusions/{entry['id']}").status_code == 200
    assert api.get("/api/staff/exclusions").json()["exclusions"] == []
    assert api.delete(f"/api/staff/exclusions/{entry['id']}").status_code == 404


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["owner", "reason"])
def test_post_requires_reason_and_owner(api: TestClient, missing: str) -> None:
    body = {"repo": "Tools", "owner": "o", "reason": "r"}
    del body[missing]
    assert api.post("/api/staff/exclusions", json=body).status_code == 422


@pytest.mark.unit
def test_write_auth_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(tmp_path / "id"))
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    app = FastAPI()
    app.include_router(staff_exclusions.router, prefix="/api/staff")
    with TestClient(app) as c:
        body = {"repo": "Tools", "owner": "o", "reason": "r"}
        assert c.post("/api/staff/exclusions", json=body, headers=_XHR).status_code in (401, 403)
        assert c.delete("/api/staff/exclusions/x", headers=_XHR).status_code in (401, 403)
    assert ExclusionsList().load() == []


# --- dispatch enforcement ---------------------------------------------------


def _run(client: TestClient, **body: Any) -> Any:
    return client.post("/api/staff/ad-hoc/run", json={"prompt": "x", "dry_run": True, **body}, headers=_XHR)


@pytest.mark.unit
def test_dispatch_blocked_by_matching_exclusion(client: TestClient) -> None:
    exp = _iso(timedelta(hours=2))
    ExclusionsList().add(
        {"repo": "D-sorganization/Tools", "issue": 9, "owner": "peer-codex", "reason": "lane", "expires_at": exp}
    )
    res = _run(client, repo="Tools", issue=9)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "dispatch_excluded"
    assert detail["owner"] == "peer-codex" and detail["reason"] == "lane" and detail["expires_at"] == exp


@pytest.mark.unit
def test_dispatch_not_blocked_when_not_matching(client: TestClient) -> None:
    ExclusionsList().add({"repo": "Tools", "issue": 9, "owner": "p", "reason": "r"})
    assert _run(client, repo="Tools", issue=10).status_code == 200
    assert _run(client, repo="Gasification", issue=9).status_code == 200
    assert _run(client).status_code == 200


@pytest.mark.unit
def test_dispatch_not_blocked_by_expired_exclusion(client: TestClient) -> None:
    ExclusionsList().add({"repo": "Tools", "issue": 9, "owner": "p", "reason": "r", "expires_at": _iso(-timedelta(1))})
    assert _run(client, repo="Tools", issue=9).status_code == 200


@pytest.mark.unit
def test_whole_repo_exclusion_blocks_dispatch(client: TestClient) -> None:
    ExclusionsList().add({"repo": "Tools", "owner": "p", "reason": "r"})
    assert _run(client, repo="Tools", issue=1).status_code == 409
    assert _run(client, repo="Tools").status_code == 409


# --- scheduler / summary ----------------------------------------------------


@pytest.mark.unit
def test_scheduler_tick_skips_excluded_repo(sched: Any) -> None:  # noqa: F811
    s, runner, clock = sched
    ExclusionsList().add({"repo": "UpstreamDrift", "owner": "peer", "reason": "mid-refactor"})
    assert s.tick() == []  # anchors the cursor
    clock["now"] = _la(2026, 9, 22, 22, 0, 10)
    decisions = s.tick()
    assert runner.submitted == []
    assert len(decisions) == 1 and decisions[0]["fired"] is False
    assert decisions[0]["reason"] == "excluded: peer: mid-refactor"


@pytest.mark.unit
def test_active_exclusions_count_for_summary() -> None:
    lst = ExclusionsList()
    lst.add({"repo": "A", "owner": "p", "reason": "r"})
    lst.add({"repo": "B", "owner": "p", "reason": "r", "expires_at": _iso(-timedelta(hours=1))})
    assert len(excl_mod.active_exclusions()) == 1
