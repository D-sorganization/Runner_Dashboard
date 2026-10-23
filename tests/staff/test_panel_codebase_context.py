"""Panels share one persisted cited codebase snapshot across all seats."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from staff import panel
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel_models import PanelCreateRequest, TurnOutcome

EXPERTS = [{"name": name, "perspective": "review"} for name in ("Astra", "Argon", "Fable")]
CONTEXT = (
    "## Codebase & Architecture Context (Cited)\n[ref:desk:codebase:UpstreamDrift@12345678:SPEC.md#L1-L2]\nContract"
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    reset_conversation_store()
    yield get_conversation_store()
    reset_conversation_store()


def test_request_defaults_and_rejects_unsupported_pack_or_repositories() -> None:
    request = PanelCreateRequest(topic="solver", experts=EXPERTS)
    assert request.pack_id == "cartographer" and request.preload_codebase and request.repos is None
    for fields in ({"pack_id": "../secrets"}, {"repos": ["../UpstreamDrift"]}, {"repos": []}):
        with pytest.raises(ValidationError):
            PanelCreateRequest(topic="solver", experts=EXPERTS, **fields)


def test_one_snapshot_is_persisted_and_injected_in_experts_and_moderator(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    preload = Mock(return_value=CONTEXT)
    monkeypatch.setattr(panel, "_preload_codebase_context", preload)
    monkeypatch.setattr(panel.cli_projects, "start_panel_sweep", lambda _: None)
    request = PanelCreateRequest(topic="solver", experts=EXPERTS, rounds=2, mode="brainstorm")
    thread = panel.start_panel_thread(request, created_by="operator", store=store)
    assert thread.meta["panel"]["codebase_context"] == CONTEXT
    prompts = []

    async def runner(speaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        prompts.append(prompt)
        return TurnOutcome(ok=True, text="STANCE: partly\nPOSITION: test")

    asyncio.run(panel.run_panel(thread.id, request, runner=runner))
    preload.assert_called_once_with(request)
    assert len(prompts) == 7
    assert all(prompt.count(CONTEXT) == 1 for prompt in prompts)
    assert store.get_thread(thread.id).meta["panel"]["codebase_context"] == CONTEXT


@pytest.mark.parametrize("fields", [{"preload_codebase": False}, {"pack_id": None}, {}])
def test_opt_out_and_missing_pack_never_cold_scan(store, monkeypatch: pytest.MonkeyPatch, fields: dict) -> None:
    preload = Mock(side_effect=AssertionError("must not scan checkouts"))
    monkeypatch.setattr("knowledge_pack.cartographer.preload_panel_context", preload)
    thread = panel.start_panel_thread(PanelCreateRequest(topic="solver", experts=EXPERTS, **fields), "operator", store)
    assert thread.meta["panel"]["codebase_context"] == ""
    preload.assert_not_called()


def test_preload_uses_only_requested_existing_checkout_and_bound(
    store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "cartographer.sqlite").touch()
    root = tmp_path / "checkout"
    root.mkdir()
    lookup = Mock(return_value=root)
    preload = Mock(return_value=CONTEXT)
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lookup)
    monkeypatch.setattr("knowledge_pack.cartographer.preload_panel_context", preload)
    request = PanelCreateRequest(topic="solver", experts=EXPERTS, repos=["UpstreamDrift"])
    assert panel._preload_codebase_context(request) == CONTEXT
    lookup.assert_called_once_with("UpstreamDrift")
    preload.assert_called_once_with({"UpstreamDrift": root}, "solver", max_tokens=800)


def test_real_committed_pack_provides_bounded_reproducible_panel_reference(
    store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knowledge_pack.cartographer import build_cartographer_pack

    root = tmp_path / "UpstreamDrift"
    root.mkdir()
    (root / "SPEC.md").write_text("# Solver contract\nThe solver preserves energy and validates time steps.\n")
    for args in (
        ("init", "-q"),
        ("add", "SPEC.md"),
        ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    build_cartographer_pack({"UpstreamDrift": root}, tmp_path / "knowledge" / "cartographer.sqlite")
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _: root)
    request = PanelCreateRequest(topic="solver energy", experts=EXPERTS, repos=["UpstreamDrift"])
    thread = panel.start_panel_thread(request, "operator", store)
    context = thread.meta["panel"]["codebase_context"]
    assert len(context.encode("utf-8")) <= 800
    assert f":codebase:UpstreamDrift@{commit[:8]}:SPEC.md#L" in context
    assert "solver preserves energy" in context
