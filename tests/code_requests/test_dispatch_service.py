"""The one code-request dispatch core shared by the legacy route and the request kind (#1501)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from code_requests.dispatch_service import CodeDispatch, resolve_dispatch, run_code_dispatch
from code_requests.profiles import AgentProfile, AgentProfileStore


@pytest.fixture
def profiles(tmp_path: Path) -> AgentProfileStore:
    store = AgentProfileStore(profiles_path=tmp_path / "profiles.json")
    store.create(
        AgentProfile(
            id="strict",
            name="Strict",
            provider="claude_code_cli",
            model="opus",
            effort="high",
            standards=["tdd", "dbc"],
            budget={"max_cost": 2.0},
        )
    )
    return store


def test_explicit_settings_win_over_the_profile(profiles: AgentProfileStore) -> None:
    req = CodeDispatch(
        repo="Tools",
        branch="main",
        prompt="x",
        profile_id="strict",
        provider="codex_cli",
        model="gpt",
        effort="low",
        standards=["lod"],
        budget={"max_cost": 0.5},
    )

    got = resolve_dispatch(req, profiles)

    assert (got.provider, got.model, got.effort) == ("codex_cli", "gpt", "low")
    assert got.standards == ["lod"]
    assert got.budget == {"max_cost": 0.5}
    assert got.profile_id == "strict"


def test_missing_settings_take_the_profile_defaults(profiles: AgentProfileStore) -> None:
    got = resolve_dispatch(CodeDispatch(repo="Tools", branch="main", prompt="x", profile_id="strict"), profiles)

    assert (got.provider, got.model, got.effort) == ("claude_code_cli", "opus", "high")
    assert got.standards == ["tdd", "dbc"]
    assert got.budget == {"max_cost": 2.0}
    assert got.profile_snapshot is not None and got.profile_snapshot["id"] == "strict"


def test_an_explicit_empty_standards_list_is_kept(profiles: AgentProfileStore) -> None:
    req = CodeDispatch(repo="Tools", branch="main", prompt="x", profile_id="strict", standards=[])

    assert resolve_dispatch(req, profiles).standards == []


class _Trigger:
    def __init__(self, code: int = 0, stderr: str = "") -> None:
        self.code, self.stderr = code, stderr
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, repo: str, branch: str, provider: str, full_prompt: str, **kw: Any) -> tuple[int, str]:
        self.calls.append({"repo": repo, "branch": branch, "provider": provider, "full_prompt": full_prompt, **kw})
        return self.code, self.stderr


def _run(req: CodeDispatch, tmp_path: Path, profiles: AgentProfileStore, trigger: _Trigger) -> Any:
    return asyncio.run(
        run_code_dispatch(
            req,
            principal="human:op",
            profile_store=profiles,
            prompt_notes_path=tmp_path / "notes.json",
            history_path=tmp_path / "history.json",
            trigger_fn=trigger,
        )
    )


def test_dispatch_builds_the_full_prompt_and_records_history(tmp_path: Path, profiles: AgentProfileStore) -> None:
    (tmp_path / "notes.json").write_text(json.dumps({"notes": "House rules.", "enabled": True}), encoding="utf-8")
    trigger = _Trigger()

    out = _run(
        CodeDispatch(repo="Tools", branch="dev", prompt="Add a CLI", profile_id="strict"), tmp_path, profiles, trigger
    )

    call = trigger.calls[0]
    assert call["full_prompt"].startswith("House rules.\n\nAdd a CLI")
    assert "## Engineering Standards" in call["full_prompt"] and "[TDD]" in call["full_prompt"]
    assert call["standards"] == ["tdd", "dbc"]
    assert (call["effort"], call["budget"], call["profile_id"], call["principal"]) == (
        "high",
        {"max_cost": 2.0},
        "strict",
        "human:op",
    )
    history = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert [e["status"] for e in history] == ["dispatched"]
    assert history[0]["id"] == out.entry["id"]
    assert history[0]["standards"] == ["tdd", "dbc"]
    assert out.code == 0


def test_a_failed_dispatch_is_recorded_as_failed(tmp_path: Path, profiles: AgentProfileStore) -> None:
    out = _run(CodeDispatch(repo="Tools", branch="main", prompt="x"), tmp_path, profiles, _Trigger(1, "no workflow"))

    history = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert history[0]["status"] == "failed" and history[0]["error"] == "no workflow"
    assert out.code == 1


@pytest.mark.parametrize("code", [422, 429])
def test_a_rejected_or_rate_limited_dispatch_is_not_recorded(
    tmp_path: Path, profiles: AgentProfileStore, code: int
) -> None:
    out = _run(CodeDispatch(repo="Tools", branch="main", prompt="x"), tmp_path, profiles, _Trigger(code, "slow down"))

    assert out.code == code
    assert out.entry is None
    assert not (tmp_path / "history.json").exists()
