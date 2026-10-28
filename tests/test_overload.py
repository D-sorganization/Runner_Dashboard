"""Tests for per-repo swarm overload detection and auto-DRAIN mode (issue #3293, slices 1-2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import coordination.overload as overload_mod
import gh_client
import pr_inventory
import pytest
from coordination.overload import (
    CONSECUTIVE_WINDOWS_THRESHOLD,
    DEFAULT_DRAIN_EXCEPTIONS,
    DEFAULT_OPEN_COUNT_THRESHOLD,
    DEFAULT_PR_SIZE_CAP,
    MODE_DRAIN,
    MODE_NORMAL,
    build_overload_snapshot,
    is_drain_open_pr_allowed,
    read_drain_state_file,
    reset_drain_state,
    suggest_pr_consolidations,
    write_drain_state_file,
)
from delivery_metrics.adapters.github import GhClientDeliveryCollector
from delivery_metrics.models import PrSnapshot

REPO = "D-sorganization/Runner_Dashboard"


def _pr(*, number: int, created_at: datetime, merged_at: datetime | None = None, state: str = "open") -> PrSnapshot:
    return PrSnapshot(
        repo_full=REPO,
        number=number,
        title=f"pr {number}",
        state=state,
        merged=merged_at is not None,
        draft=False,
        head_sha="deadbeef",
        base_ref="main",
        author_login="someone",
        created_at=created_at,
        updated_at=merged_at or created_at,
        closed_at=merged_at,
        merged_at=merged_at,
        additions=1,
        deletions=1,
        changed_files=1,
        html_url=f"https://github.com/{REPO}/pull/{number}",
    )


async def _empty_paginate(_path: str):
    return
    yield  # pragma: no cover - makes this an async generator


def _fake_paginate_n(count: int):
    async def _gen(_path: str):
        for i in range(count):
            yield {"id": i}

    return _gen


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import cache_utils

    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("overload-unit"))
    reset_drain_state()


@pytest.fixture(autouse=True)
def _default_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy, empty-by-default stand-ins for every GitHub-backed source."""

    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict], str | None]:
        return [], None

    async def _fake_list_pulls_updated_since(self, _repo, _since, *, max_pages: int = 3) -> tuple[PrSnapshot, ...]:
        return ()

    async def _fake_get(_path: str) -> Any:
        return []

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setattr(GhClientDeliveryCollector, "list_pulls_updated_since", _fake_list_pulls_updated_since)
    monkeypatch.setattr(gh_client, "get", _fake_get)
    monkeypatch.setattr(gh_client, "paginate", _empty_paginate)


@pytest.mark.asyncio
async def test_build_overload_snapshot_combines_every_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict], str | None]:
        return [{"number": 1}, {"number": 2}], None

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setattr(gh_client, "paginate", _fake_paginate_n(4))

    async def _fake_get(path: str) -> Any:
        if path.endswith("/rulesets"):
            return [{"id": 7, "enforcement": "active"}]
        return {
            "rules": [
                {
                    "type": "merge_queue",
                    "parameters": {
                        "max_entries_to_build": 5,
                        "max_entries_to_merge": 5,
                        "min_entries_to_merge": 1,
                        "min_entries_to_merge_wait_minutes": 5,
                        "check_response_timeout_minutes": 60,
                    },
                }
            ]
        }

    monkeypatch.setattr(gh_client, "get", _fake_get)

    snapshot = await build_overload_snapshot(REPO)

    assert snapshot["repo"] == REPO
    assert snapshot["open_pr_count"] == 2
    assert snapshot["merge_queue_depth"] == 4
    assert snapshot["merge_queue_settings"] == {
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 5,
        "check_response_timeout_minutes": 60,
    }
    assert "1h" in snapshot["windows"]
    assert "6h" in snapshot["windows"]
    assert snapshot["overloaded"] is False
    assert snapshot["overload_reasons"] == []
    assert snapshot["warnings"] == []


@pytest.mark.asyncio
async def test_overloaded_by_open_count_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict], str | None]:
        return [{"number": i} for i in range(20)], None

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)

    snapshot = await build_overload_snapshot(REPO, open_count_threshold=15)

    assert snapshot["open_pr_count"] == 20
    assert snapshot["overloaded"] is True
    assert any("exceeds threshold 15" in reason for reason in snapshot["overload_reasons"])


@pytest.mark.asyncio
async def test_overloaded_by_consecutive_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    prs: list[PrSnapshot] = []
    number = 0
    for bucket in range(CONSECUTIVE_WINDOWS_THRESHOLD):  # 2 opened/0 merged per trailing 1h bucket
        for _ in range(2):
            number += 1
            created = now - timedelta(hours=bucket, minutes=30)
            prs.append(_pr(number=number, created_at=created))

    async def _fake_list_pulls_updated_since(self, _repo, _since, *, max_pages: int = 3) -> tuple[PrSnapshot, ...]:
        return tuple(prs)

    monkeypatch.setattr(GhClientDeliveryCollector, "list_pulls_updated_since", _fake_list_pulls_updated_since)

    snapshot = await build_overload_snapshot(REPO, open_count_threshold=1000)

    assert snapshot["overloaded"] is True
    assert any("consecutive" in reason for reason in snapshot["overload_reasons"])


@pytest.mark.asyncio
async def test_windows_count_opened_and_merged_events() -> None:
    now = datetime.now(UTC)
    prs = (
        _pr(number=1, created_at=now - timedelta(minutes=10)),
        _pr(number=2, created_at=now - timedelta(hours=3), merged_at=now - timedelta(minutes=5)),
        _pr(number=3, created_at=now - timedelta(hours=10)),  # outside any window
    )
    windows = overload_mod._windows(prs, now=now)

    assert windows["1h"]["open_count"] == 1
    assert windows["1h"]["merge_count"] == 1
    assert windows["6h"]["open_count"] == 2
    assert windows["6h"]["merge_count"] == 1


@pytest.mark.asyncio
async def test_degrades_independently_when_a_source_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_repo: str) -> tuple[list[dict], str | None]:
        raise RuntimeError("github down")

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _boom)

    snapshot = await build_overload_snapshot(REPO)

    assert snapshot["open_pr_count"] == 0
    assert any("open PR count unavailable" in w for w in snapshot["warnings"])
    # the rest of the snapshot is still assembled
    assert "windows" in snapshot
    assert snapshot["merge_queue_depth"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("repo", "open_count_threshold"), [("not-a-slug", DEFAULT_OPEN_COUNT_THRESHOLD), (REPO, 0)])
async def test_rejects_bad_contract_input(repo: str, open_count_threshold: int) -> None:
    with pytest.raises(ValueError):
        await build_overload_snapshot(repo, open_count_threshold=open_count_threshold)


@pytest.mark.asyncio
async def test_no_active_merge_queue_ruleset_reported_as_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get(path: str) -> Any:
        if path.endswith("/rulesets"):
            return [{"id": 7, "enforcement": "active"}]
        return {"rules": [{"type": "creation"}]}

    monkeypatch.setattr(gh_client, "get", _fake_get)

    snapshot = await build_overload_snapshot(REPO)

    assert snapshot["merge_queue_settings"] is None
    assert any("no active ruleset defines a merge_queue rule" in w for w in snapshot["warnings"])


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer
    from server import app  # noqa: PLC0415

    async def _fake_build_overload_snapshot(
        repo: str, *, open_count_threshold: int = DEFAULT_OPEN_COUNT_THRESHOLD
    ) -> dict:
        return {
            "repo": repo,
            "open_pr_count": 3,
            "windows": {},
            "merge_queue_depth": 0,
            "merge_queue_settings": None,
            "overloaded": False,
            "overload_reasons": [],
            "open_count_threshold": open_count_threshold,
            "consecutive_windows_threshold": CONSECUTIVE_WINDOWS_THRESHOLD,
            "warnings": [],
        }

    monkeypatch.setattr("routers.overload.build_overload_snapshot", _fake_build_overload_snapshot)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)


def test_get_fleet_overload_returns_snapshot(client: Any) -> None:
    resp = client.get("/api/v1/fleet/overload", params={"repo": REPO})
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == REPO
    assert body["open_pr_count"] == 3


@pytest.mark.parametrize("params", [{}, {"repo": "not-a-slug"}])
def test_get_fleet_overload_rejects_bad_input(client: Any, params: dict[str, str]) -> None:
    resp = client.get("/api/v1/fleet/overload", params=params)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_auto_drain_publishes_when_overloaded_and_clears_after_full_recovery_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    open_prs: list[dict[str, int]] = [{"number": i} for i in range(20)]
    history_prs: tuple[PrSnapshot, ...] = ()

    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict[str, int]], str | None]:
        return open_prs, None

    async def _fake_list_pulls_updated_since(
        self: Any, _repo: str, _since: datetime, *, max_pages: int = 3
    ) -> tuple[PrSnapshot, ...]:
        return history_prs

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setattr(GhClientDeliveryCollector, "list_pulls_updated_since", _fake_list_pulls_updated_since)

    # Step 1: Overloaded -> enters DRAIN mode and publishes lane_notice + dispatch_directive.
    snap1 = await build_overload_snapshot(REPO, open_count_threshold=15)
    assert snap1["overloaded"] is True
    assert snap1["mode"] == MODE_DRAIN
    assert snap1["drain"]["active"] is True
    assert snap1["drain"]["mode"] == MODE_DRAIN
    assert snap1["drain"]["open_pr_exceptions"] == list(DEFAULT_DRAIN_EXCEPTIONS)
    assert f"OVERLOAD_MODE=DRAIN repo={REPO}" in snap1["lane_notice"]
    assert "rebase,fix,arm-only" in snap1["lane_notice"]
    assert snap1["dispatch_directive"]["allow_new_prs"] is False

    # Step 2: Open count drops below threshold, but 1h merge_rate == open_rate (not >) -> stays in DRAIN.
    open_prs = [{"number": 1}, {"number": 2}]
    history_prs = (
        _pr(number=10, created_at=now - timedelta(minutes=20)),
        _pr(number=11, created_at=now - timedelta(hours=2), merged_at=now - timedelta(minutes=15)),
    )
    snap2 = await build_overload_snapshot(REPO, open_count_threshold=15)
    assert snap2["overloaded"] is False
    assert snap2["mode"] == MODE_DRAIN
    assert snap2["drain"]["active"] is True
    assert snap2["drain"]["latched"] is True

    # Step 3: 1h merge_rate > open_rate for a full window while not overloaded -> DRAIN clears automatically.
    history_prs = (
        _pr(number=10, created_at=now - timedelta(minutes=20)),
        _pr(number=11, created_at=now - timedelta(hours=2), merged_at=now - timedelta(minutes=15)),
        _pr(number=12, created_at=now - timedelta(hours=3), merged_at=now - timedelta(minutes=5)),
    )
    snap3 = await build_overload_snapshot(REPO, open_count_threshold=15)
    assert snap3["overloaded"] is False
    assert snap3["mode"] == MODE_NORMAL
    assert snap3["drain"]["active"] is False
    assert snap3["drain"]["cleared"] is True
    assert snap3["lane_notice"] == f"OVERLOAD_MODE=NORMAL repo={REPO}"
    assert snap3["dispatch_directive"]["allow_new_prs"] is True


@pytest.mark.asyncio
async def test_configurable_drain_exceptions_and_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict[str, int]], str | None]:
        return [{"number": i} for i in range(20)], None

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setenv("RD_OVERLOAD_DRAIN_EXCEPTIONS", "ci-unblock, security , work-report")

    snap = await build_overload_snapshot(REPO, open_count_threshold=15)
    assert snap["drain"]["open_pr_exceptions"] == ["ci-unblock", "security", "work-report"]
    assert is_drain_open_pr_allowed(snap, category=None) is False
    assert is_drain_open_pr_allowed(snap, category="feature") is False
    assert is_drain_open_pr_allowed(snap, category="ci-unblock") is True
    assert is_drain_open_pr_allowed(snap, category="SECURITY") is True

    with pytest.raises(ValueError, match="drain_exceptions"):
        await build_overload_snapshot(REPO, drain_exceptions=("",))


@pytest.mark.asyncio
async def test_drain_state_file_roundtrip_for_lane_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict[str, int]], str | None]:
        return [{"number": i} for i in range(18)], None

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    snap = await build_overload_snapshot(REPO, open_count_threshold=15)

    state_file = tmp_path / "overload-drain.json"
    written = write_drain_state_file(state_file, snap)
    assert written == state_file
    loaded = read_drain_state_file(state_file)
    assert loaded["repo"] == REPO
    assert loaded["mode"] == MODE_DRAIN
    assert loaded["drain"]["active"] is True
    assert loaded["lane_notice"] == snap["lane_notice"]


def test_suggest_pr_consolidations_groups_by_issue_branch_and_paths_under_size_cap() -> None:
    open_prs = [
        {
            "number": 101,
            "title": "fix(overload): part 1",
            "head_ref": "claude/3100-overload-part1",
            "linked_issues": [3100],
            "additions": 120,
            "deletions": 30,
            "changed_paths": ["backend/coordination/overload.py"],
        },
        {
            "number": 102,
            "title": "fix(overload): part 2",
            "head_ref": "agy/3100-overload-part2",
            "body": "Closes #3100",
            "additions": 180,
            "deletions": 20,
            "changed_paths": ["backend/coordination/overload.py", "tests/test_overload.py"],
        },
        {
            "number": 103,
            "title": "fix(overload): part 3 too large to fit cap",
            "head_ref": "codex/3100-overload-part3",
            "linked_issues": [3100],
            "additions": 160,
            "deletions": 40,
            "changed_paths": ["backend/coordination/overload.py"],
        },
        {
            "number": 201,
            "title": "chore(topology): sanitize slice 04",
            "head_ref": "cursor/topology-sanitize-04-a43e",
            "linked_issues": [],
            "changed_lines": 140,
            "changed_paths": ["docs/a.md"],
        },
        {
            "number": 202,
            "title": "chore(topology): sanitize slice 07",
            "head_ref": "cursor/topology-sanitize-07-a43e",
            "linked_issues": [],
            "changed_lines": 190,
            "changed_paths": ["docs/b.md"],
        },
        {
            "number": 301,
            "title": "fix(staff): tweak router validation",
            "head_ref": "claude/2879-fleet-glance",
            "linked_issues": [],
            "changed_lines": 80,
            "changed_paths": ["backend/routers/staff.py"],
        },
        {
            "number": 302,
            "title": "refactor(staff): cleanup router error path",
            "head_ref": "claude/2664-lesson-locator",
            "linked_issues": [],
            "changed_lines": 110,
            "changed_paths": ["backend/routers/staff.py", "tests/test_staff.py"],
        },
        {
            "number": 401,
            "title": "feat(unrelated): standalone change",
            "head_ref": "claude/2999-unrelated-feature",
            "linked_issues": [2999],
            "changed_lines": 50,
            "changed_paths": ["backend/routers/other.py"],
        },
    ]

    suggestions = suggest_pr_consolidations(open_prs, size_cap=DEFAULT_PR_SIZE_CAP)
    assert len(suggestions) == 3

    by_survivor = {item["surviving_pr"]: item for item in suggestions}

    # Group 1: linked issue #3100 -> 101 (150 lines) + 102 (200 lines) = 350 <= 500; 103 (200 lines) skipped over cap.
    s_issue = by_survivor[101]
    assert s_issue["group_by"] == "linked_issue"
    assert s_issue["absorbed_prs"] == [102]
    assert s_issue["combined_changed_lines"] == 350
    assert s_issue["skipped_over_cap_prs"] == [103]
    assert s_issue["shared_issues"] == [3100]
    assert "backend/coordination/overload.py" in s_issue["shared_paths"]
    assert s_issue["auto_close"] is False

    # Group 2: branch prefix cursor/topology-sanitize -> 201 (140) + 202 (190) = 330 <= 500.
    s_branch = by_survivor[201]
    assert s_branch["group_by"] == "branch_prefix"
    assert s_branch["absorbed_prs"] == [202]
    assert s_branch["combined_changed_lines"] == 330
    assert s_branch["branch_prefix"] == "cursor/topology-sanitize"
    assert s_branch["auto_close"] is False

    # Group 3: overlapping paths backend/routers/staff.py -> 301 (80) + 302 (110) = 190 <= 500.
    # Unrelated PR 401 is not grouped with 301/302 despite sharing the 'claude/' agent prefix.
    s_paths = by_survivor[301]
    assert s_paths["group_by"] == "overlapping_paths"
    assert s_paths["absorbed_prs"] == [302]
    assert s_paths["combined_changed_lines"] == 190
    assert s_paths["shared_paths"] == ["backend/routers/staff.py"]
    assert s_paths["auto_close"] is False


@pytest.mark.asyncio
async def test_build_overload_snapshot_includes_consolidation_suggestions_with_recent_pr_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)

    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict[str, Any]], str | None]:
        return [
            {"number": 501, "title": "slice a", "head_ref": "agy/3293-overload-a", "linked_issues": [3293]},
            {"number": 502, "title": "slice b", "head_ref": "agy/3293-overload-b", "linked_issues": [3293]},
        ], None

    async def _fake_list_pulls_updated_since(
        self: Any, _repo: str, _since: datetime, *, max_pages: int = 3
    ) -> tuple[PrSnapshot, ...]:
        p1 = _pr(number=501, created_at=now - timedelta(minutes=30))
        p2 = _pr(number=502, created_at=now - timedelta(minutes=20))
        return (
            PrSnapshot(**{**p1.__dict__, "additions": 100, "deletions": 25}),
            PrSnapshot(**{**p2.__dict__, "additions": 150, "deletions": 50}),
        )

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setattr(GhClientDeliveryCollector, "list_pulls_updated_since", _fake_list_pulls_updated_since)

    snap = await build_overload_snapshot(REPO)
    assert snap["pr_size_cap"] == DEFAULT_PR_SIZE_CAP
    assert len(snap["consolidation_suggestions"]) == 1
    suggestion = snap["consolidation_suggestions"][0]
    assert suggestion["surviving_pr"] == 501
    assert suggestion["absorbed_prs"] == [502]
    assert suggestion["combined_changed_lines"] == 325
    assert suggestion["auto_close"] is False

    with pytest.raises(ValueError, match="size_cap"):
        suggest_pr_consolidations([], size_cap=0)
