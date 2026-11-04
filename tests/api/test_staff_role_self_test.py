"""API tests for Desk staff role self-test endpoints (#3847)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal
from staff import adapters as adapters_mod
from staff import roles as roles_mod
from staff import runner as runner_mod
from staff import runner_registry
from staff import store as store_mod
from staff.dispatch_auth import require_staff_run_dispatch
from staff.rate_limit import reset_rate_limiter

from tests.api.test_staff_runner import _FAKE_CLI  # noqa: PLC2701


def _write_desk_roles(roles_dir: Path) -> None:
    roles_dir.mkdir(exist_ok=True)
    for name in (
        "bob",
        "night-watch",
        "issue-remediator",
        "pr-remediator",
        "cartographer",
        "board-secretary",
        "sanitation",
    ):
        (roles_dir / f"{name}.yml").write_text(
            "\n".join(
                [
                    f"name: {name}",
                    f"title: {name}",
                    "summary: self-test fixture",
                    "playbook: docs/fleet-night-watch.md",
                    "prompt_template: null",
                    "instructions: Self-test fixture role.",
                    "providers: [fake]",
                    "model: default",
                    "schedule: '0 22 * * *'",
                    "window: {start: '22:00', end: '06:00'}",
                    "repos: [Runner_Dashboard]",
                    "budget: {usd_per_run: 1.0, usd_per_day: 5.0}",
                    "permissions: {push_branch: false, open_pr: false, merge: false}",
                ]
            ),
            encoding="utf-8",
        )


@pytest.fixture
def self_test_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(_FAKE_CLI, encoding="utf-8")
    roles_dir = tmp_path / "roles"
    _write_desk_roles(roles_dir)

    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles_dir))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path / "nonexistent-repos"))
    reset_rate_limiter()
    fake_adapter = adapters_mod.ProviderAdapter(
        provider_id="fake",
        label="Fake",
        executable=sys.executable,
        argv=(str(fake_cli), "{prompt}", "--model", "{model}"),
        json_lines=True,
    )
    adapters = {**adapters_mod.ADAPTERS, "fake": fake_adapter}
    monkeypatch.setattr(adapters_mod, "ADAPTERS", adapters)
    roles_mod._cached_roles = None  # noqa: SLF001
    runner_registry.reset_runner()
    store_mod.reset_store()
    store = store_mod.RunStore(tmp_path / "runs.sqlite3")
    runner = runner_mod.StaffRunner(store=store, adapters=adapters, machine="TestNode")
    monkeypatch.setattr(runner_registry, "_runner", runner)

    from routers import staff_role_self_test as self_test_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(self_test_router.router)
    app.dependency_overrides[require_staff_run_dispatch()] = lambda: Principal(
        id="test-dispatch", type="bot", name="Test Dispatch", roles=["admin"]
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    runner_registry.reset_runner()
    store_mod.reset_store()
    reset_rate_limiter()


def test_desk_core_self_test_dry_run(self_test_client: TestClient) -> None:
    res = self_test_client.post("/api/staff/self-test/desk-core")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert len(body["results"]) == 7
    assert all(r["ok"] for r in body["results"])


def test_single_role_self_test_with_persona_drill(self_test_client: TestClient) -> None:
    res = self_test_client.post("/api/staff/bob/self-test?persona_drill=true")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["checks"]["dispatch_dry_run"]["ok"] is True
    assert body["checks"]["persona_drill"]["ok"] is True


def test_self_test_rejects_principal_without_dispatch_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from identity import Principal, require_principal  # noqa: PLC0415
    from routers import staff_role_self_test as self_test_router  # noqa: PLC0415

    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))
    store_mod.reset_store()
    runner_registry.reset_runner()

    app = FastAPI()
    app.include_router(self_test_router.router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="viewer-user", type="user", name="Viewer", roles=["viewer"]
    )
    with TestClient(app) as client:
        resp = client.post("/api/staff/bob/self-test")
    app.dependency_overrides.clear()
    roles_mod._cached_roles = None  # noqa: SLF001
    runner_registry.reset_runner()
    store_mod.reset_store()
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_scope"] == "staff.dispatch"


def test_desk_core_self_test_does_not_persist_runs(self_test_client: TestClient) -> None:
    store = runner_mod.get_runner().store
    before = len(store.list_runs(limit=500))
    res = self_test_client.post("/api/staff/self-test/desk-core")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert len(store.list_runs(limit=500)) == before
