"""A real runner stores a same-provider review's outcome with its mark (#1579).

The runner only sees the run record when it parses the verdict, so the flag set at
selection time must travel on the run itself (the prompt tag) to reach ``outcome``.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from staff import adapters as adapters_mod
from staff import runner as runner_mod
from staff import store as store_mod
from staff import workspace as workspace_mod
from staff.review import prepare_review_params
from staff.runner import RunRequest

_FAKE_CLI = """
import json
result = "STAFF_RESULT: review approve #42"
print(json.dumps({"type": "result", "result": result, "usage": {"input_tokens": 1, "output_tokens": 1}}))
"""


class _Trailers:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def get_commit_messages(self, _repo: str, _pr: int) -> list[str]:
        return [f"fix: y\n\nAgent-Id: {self.provider}"]


@pytest.fixture
def staff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[runner_mod.StaffRunner]:
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "code-reviewer.yml").write_text(
        "name: code-reviewer\ntitle: Code Reviewer\nproviders: [fake]\npermissions: {lease: false}\n",
        encoding="utf-8",
    )
    script = tmp_path / "fake_cli.py"
    script.write_text(_FAKE_CLI, encoding="utf-8")
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(roles))
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "wt"))
    fake = adapters_mod.ProviderAdapter(
        provider_id="fake", label="Fake", executable=sys.executable, argv=(str(script), "{prompt}"), json_lines=True
    )
    adapters = {**adapters_mod.ADAPTERS, "fake": fake}
    monkeypatch.setattr(adapters_mod, "ADAPTERS", adapters)
    monkeypatch.setattr(workspace_mod, "find_repo_checkout", lambda _repo: tmp_path / "checkout")
    monkeypatch.setattr(workspace_mod, "add_worktree", lambda _c, wt, _b: wt.mkdir(parents=True, exist_ok=True))
    store_mod.reset_store()
    runner_mod.reset_runner()
    r = runner_mod.StaffRunner(
        store=store_mod.RunStore(tmp_path / "runs.sqlite3"), adapters=adapters, machine="TestNode"
    )
    monkeypatch.setattr(runner_mod, "_runner", r)
    yield r
    store_mod.reset_store()
    runner_mod.reset_runner()


def _wait(store: store_mod.RunStore, run_id: str, timeout: float = 20.0) -> store_mod.RunRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = store.get_run(run_id)
        assert rec is not None
        if rec.status not in store_mod.ACTIVE_STATUSES:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still active")


def _review(staff: runner_mod.StaffRunner, author: str) -> store_mod.RunRecord:
    params = prepare_review_params(
        {"repo": "Runner_Dashboard", "pr": 42}, roster=staff.roles(), store=staff.store, gh_probe=_Trailers(author)
    )
    # Run on the fake adapter; the selection (and its tag) is what is under test.
    req = RunRequest(role=params["role"], provider="fake", repo="Runner_Dashboard", pr=42, prompt=params["prompt"])
    return _wait(staff.store, staff.submit(req).id)


@pytest.mark.unit
def test_same_provider_review_outcome_is_marked(staff: runner_mod.StaffRunner) -> None:
    rec = _review(staff, author="fake")  # the only reviewer provider is the author's

    assert rec.status == "succeeded"
    assert rec.outcome == "review approve #42 (same-provider)"


@pytest.mark.unit
def test_cross_provider_review_outcome_is_unmarked(staff: runner_mod.StaffRunner) -> None:
    rec = _review(staff, author="codex")

    assert rec.status == "succeeded"
    assert rec.outcome == "review approve #42"
