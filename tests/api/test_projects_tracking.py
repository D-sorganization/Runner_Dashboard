"""Fleet project tracking: priority tiers, charter coverage and the untracked-work report.

Priorities come from Repository_Management ``config/project_priorities.yaml``;
coverage joins charter Tracking refs against a repo's open issues and PRs; the
rollup sorts projects by tier and builds the fleet-curator worklist.
GitHub is faked at ``projects.service.gh_api`` exactly like test_projects_router.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

from cache_utils import cache_clear  # noqa: E402
from projects import coverage, priorities, rollup, service  # noqa: E402
from projects.charter import Feature  # noqa: E402
from staff import store as store_mod  # noqa: E402

PRIORITIES_YAML = """
schema_version: 1
projects:
  Beta:
    tier: P0
    focus: Ship the rollup
    rationale: Everything else depends on it
    decided: 2026-09-25
  Alpha:
    tier: P2
"""

CHARTER = """# Project Charter

## End Goal

Ship.

## Non-Goals

- Nothing else.

## Features

| ID | Feature | Status | Tracking | Notes |
| --- | --- | --- | --- | --- |
| F1 | Parser | shipped | #1 | |
| F2 | Router | in-progress | #10 | epic |
| F3 | Elsewhere | planned | D-sorganization/Other#10 | |
"""


def _feature(tracking: str, fid: str = "F1") -> Feature:
    return Feature(id=fid, feature="x", status="planned", tracking=tracking)


def _item(number: int, title: str = "t", body: str = "", pr: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "number": number,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/o/r/issues/{number}",
        "updated_at": "2026-09-20T00:00:00Z",
        "labels": [{"name": "bug"}],
    }
    if pr:
        item["pull_request"] = {"url": "x"}
    return item


# ── priorities ────────────────────────────────────────────────────────────────


def test_tier_vocabulary_pinned() -> None:
    assert priorities.TIERS == ("P0", "P1", "P2", "P3", "P4")
    assert priorities.UNRANKED == "unranked"
    assert set(priorities.TIER_MEANING) == set(priorities.TIERS)


def test_parse_priorities_reads_tiers_and_optional_fields() -> None:
    parsed = priorities.parse_priorities(PRIORITIES_YAML)
    assert parsed["Beta"].to_dict() == {
        "tier": "P0",
        "focus": "Ship the rollup",
        "rationale": "Everything else depends on it",
        "decided": "2026-09-25",
    }
    assert parsed["Alpha"].to_dict() == {"tier": "P2", "focus": "", "rationale": "", "decided": ""}


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("schema_version: 2\nprojects: {}\n", "schema_version"),
        ("schema_version: 1\nprojects: []\n", "projects"),
        ("schema_version: 1\nprojects:\n  A:\n    tier: P9\n", "invalid tier 'P9'"),
        ("schema_version: 1\nprojects:\n  ../x:\n    tier: P1\n", "invalid repository name"),
        ("[unclosed", "YAML"),
    ],
)
def test_parse_priorities_rejects_malformed(text: str, match: str) -> None:
    with pytest.raises(priorities.PriorityError, match=match):
        priorities.parse_priorities(text)


def test_tier_rank_orders_unranked_last() -> None:
    assert priorities.tier_rank("P0") < priorities.tier_rank("P4") < priorities.tier_rank(priorities.UNRANKED)
    assert priorities.tier_rank("bogus") == priorities.tier_rank(priorities.UNRANKED)


def test_unranked_default() -> None:
    assert priorities.unranked().to_dict() == {"tier": "unranked", "focus": "", "rationale": "", "decided": ""}


# ── coverage ──────────────────────────────────────────────────────────────────


def test_tracked_numbers_same_repo_only() -> None:
    features = [
        _feature("#1"),
        _feature("-", "F2"),
        _feature("D-sorganization/Alpha#7", "F3"),
        _feature("o/Other#9", "F4"),
    ]
    assert coverage.tracked_numbers(features, "Alpha") == frozenset({1, 7})


def test_classify_tracks_direct_and_referencing_items() -> None:
    features = [_feature("#10")]
    items = [
        _item(10, "Epic"),
        _item(11, "Child", body="Part of #10"),
        _item(12, "Loose issue", body="mentions #100 only"),
        _item(13, "Fix", body="Fixes #12", pr=True),
        _item(14, "Other repo ref", body="See D-sorganization/Other#10"),
    ]
    result = coverage.classify("Alpha", features, items)
    assert result["open_items"] == 5
    assert result["tracked"] == 2
    assert result["percent_tracked"] == 40
    assert [(u["number"], u["kind"]) for u in result["untracked"]] == [(12, "issue"), (13, "pr"), (14, "issue")]
    assert result["untracked_count"] == 3
    assert result["untracked"][0] == {
        "number": 12,
        "title": "Loose issue",
        "kind": "issue",
        "url": "https://github.com/o/r/issues/12",
        "updated_at": "2026-09-20T00:00:00Z",
        "labels": ["bug"],
    }


def test_classify_empty_and_cap() -> None:
    assert coverage.classify("A", [], [])["percent_tracked"] == 100
    many = [_item(n) for n in range(1, coverage.UNTRACKED_CAP + 20)]
    result = coverage.classify("A", [], many)
    assert result["untracked_count"] == len(many)
    assert len(result["untracked"]) == coverage.UNTRACKED_CAP


# ── rollup ────────────────────────────────────────────────────────────────────


def _project(repo: str, *, charter: bool = True, untracked: int = 0, **progress: int) -> dict[str, Any]:
    base = {"planned": 0, "in_progress": 0, "shipped": 0, "parked": 0, "percent_shipped": 0}
    return {
        "repo": repo,
        "charter_present": charter,
        "features": [],
        "progress": {**base, **progress},
        "decisions_needed": ["d"] if charter else [],
        "coverage": {
            "open_items": untracked,
            "tracked": 0,
            "percent_tracked": 0,
            "untracked_count": untracked,
            "untracked": [{"number": n} for n in range(untracked)],
        },
    }


def test_attach_and_sort_by_priority_keeps_config_order_within_tier() -> None:
    prio = priorities.parse_priorities(PRIORITIES_YAML)
    projects = [_project("Alpha"), _project("Gamma"), _project("Beta"), _project("Delta")]
    ordered = rollup.sort_by_priority(rollup.attach_priorities(projects, prio))
    assert [p["repo"] for p in ordered] == ["Beta", "Alpha", "Gamma", "Delta"]
    assert ordered[0]["priority"]["tier"] == "P0"
    assert ordered[2]["priority"]["tier"] == "unranked"


def test_fleet_summary_counts() -> None:
    prio = priorities.parse_priorities(PRIORITIES_YAML)
    projects = rollup.attach_priorities(
        [
            _project("Alpha", shipped=3, planned=1),
            _project("Beta", in_progress=2, untracked=4),
            _project("Gamma", charter=False, untracked=1),
        ],
        prio,
    )
    assert rollup.fleet_summary(projects) == {
        "repos": 3,
        "with_charter": 2,
        "without_charter": ["Gamma"],
        "features": {"planned": 1, "in_progress": 2, "shipped": 3, "parked": 0},
        "by_tier": {"P0": 1, "P1": 0, "P2": 1, "P3": 0, "P4": 0, "unranked": 1},
        "decisions_needed": 2,
        "untracked_items": 5,
    }


def test_untracked_report_lists_orphans_and_unregistered_repos() -> None:
    prio = priorities.parse_priorities(PRIORITIES_YAML)
    projects = rollup.attach_priorities(
        [_project("Alpha", untracked=2), _project("Beta"), _project("Gamma", charter=False)], prio
    )
    org = [
        {"name": "Alpha", "archived": False},
        {"name": "NewThing", "archived": False},
        {"name": "Old", "archived": True},
        {"name": "Beta", "archived": False},
    ]
    report = rollup.untracked_report(projects, org)
    assert report["repos_without_charter"] == ["Gamma"]
    assert report["unregistered_repos"] == ["NewThing"]
    assert report["total_untracked"] == 2
    assert [r["repo"] for r in report["repos"]] == ["Alpha"]
    assert report["repos"][0]["tier"] == "P2"


# ── service + routes ─────────────────────────────────────────────────────────


def _contents(text: str) -> dict[str, Any]:
    return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}


def _fake_gh(files: dict[str, str], issues: dict[str, list[dict[str, Any]]], org: list[dict[str, Any]]) -> Any:
    async def fake(endpoint: str) -> Any:
        for key, text in files.items():
            repo, path = key.split("/", 1)
            if endpoint == f"/repos/{service.ORG}/{repo}/contents/{path}":
                return _contents(text)
        for repo, rows in issues.items():
            if endpoint.startswith(f"/repos/{service.ORG}/{repo}/issues?"):
                return rows if "page=1" in endpoint else []
        if endpoint.startswith(f"/orgs/{service.ORG}/repos?"):
            return org if "page=1" in endpoint else []
        raise HTTPException(status_code=404, detail=f"GitHub resource not found: {endpoint}")

    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    from fastapi import FastAPI
    from routers import projects as projects_router

    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    config = tmp_path / "projects.json"
    config.write_text(json.dumps({"repos": ["Alpha", "Beta"]}), encoding="utf-8")
    monkeypatch.setattr(service, "_CONFIG_PATH", config)
    store_mod.reset_store()
    cache_clear()
    app = FastAPI()
    app.include_router(projects_router.router)
    yield TestClient(app, raise_server_exceptions=False)
    cache_clear()
    store_mod.reset_store()


def _wire(monkeypatch: pytest.MonkeyPatch, *, prio: str | None = PRIORITIES_YAML) -> None:
    files = {"Alpha/docs/project/CHARTER.md": CHARTER}
    if prio is not None:
        files[f"{priorities.PRIORITIES_REPO}/{priorities.PRIORITIES_PATH}"] = prio
    issues = {"Alpha": [_item(10), _item(11, body="Part of #10"), _item(12)], "Beta": [_item(5, pr=True)]}
    org = [
        {"name": "Alpha", "archived": False},
        {"name": "Beta", "archived": False},
        {"name": "Stray", "archived": False},
    ]
    monkeypatch.setattr(service, "gh_api", _fake_gh(files, issues, org))


def test_list_route_sorted_by_priority_with_summary(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    body = client.get("/api/projects").json()
    assert [p["repo"] for p in body["projects"]] == ["Beta", "Alpha"]
    alpha = body["projects"][1]
    assert alpha["priority"]["tier"] == "P2"
    assert alpha["coverage"]["tracked"] == 2 and alpha["coverage"]["untracked_count"] == 1
    assert body["summary"]["by_tier"]["P0"] == 1
    assert body["summary"]["untracked_items"] == 2
    assert "priorities_error" not in body


def test_list_route_without_priority_file_is_unranked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, prio=None)
    body = client.get("/api/projects").json()
    assert [p["repo"] for p in body["projects"]] == ["Alpha", "Beta"]
    assert {p["priority"]["tier"] for p in body["projects"]} == {"unranked"}
    assert body["priorities_error"] == "priority file not found"


def test_list_route_with_malformed_priority_file_reports_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, prio="schema_version: 1\nprojects:\n  Alpha:\n    tier: P7\n")
    body = client.get("/api/projects").json()
    assert body["priorities_error"].startswith("priorities invalid:")
    assert {p["priority"]["tier"] for p in body["projects"]} == {"unranked"}


def test_untracked_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    resp = client.get("/api/projects/untracked")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["unregistered_repos"] == ["Stray"]
    assert report["repos_without_charter"] == ["Beta"]
    assert [r["repo"] for r in report["repos"]] == ["Beta", "Alpha"]
    assert report["total_untracked"] == 2


def test_priorities_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    body = client.get("/api/projects/priorities").json()
    assert body["tiers"] == [{"tier": t, "meaning": priorities.TIER_MEANING[t]} for t in priorities.TIERS]
    assert body["projects"]["Beta"]["tier"] == "P0"
    assert body["source"] == f"{priorities.PRIORITIES_REPO}/{priorities.PRIORITIES_PATH}"


def test_coverage_failure_is_reported_not_raised(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {"Alpha/docs/project/CHARTER.md": CHARTER}

    async def fake(endpoint: str) -> Any:
        if "/issues?" in endpoint:
            raise HTTPException(status_code=502, detail="GitHub API error: boom")
        return await _fake_gh(files, {}, [])(endpoint)

    monkeypatch.setattr(service, "gh_api", fake)
    alpha = client.get("/api/projects/Alpha").json()
    assert alpha["charter_present"] is True
    assert alpha["coverage"] is None
    assert alpha["coverage_error"] == "github: GitHub API error: boom"
