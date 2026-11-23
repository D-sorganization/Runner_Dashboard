"""Cross-repo presence switch after release (issue #4624)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from coordination_fake_rm import FakeRM, board, bot, install, session
from fastapi.testclient import TestClient

_BOT = {"Authorization": "Bearer bot-token", "X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools,Games")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


def _argv(rm: FakeRM) -> list[list[str]]:
    return [argv for name, argv in rm.calls() if name == "agent_communicate"]


@pytest.mark.unit
def test_presence_switch_releases_bound_repo_before_cross_repo_register(
    rm: FakeRM, client: TestClient
) -> None:
    rm.respond("agent_communicate:list", board(session("codex-1", "Tools", agent="codex")))
    rm.respond("agent_communicate:release", {"ok": True})
    rm.respond("agent_communicate:register", {"ok": True, "receipt": "c-1", "event": {"id": "e1"}})
    rel = {"session": "codex-1", "repo": "Games"}
    assert client.post("/api/coordination/presence/release", json=rel, headers=_BOT).status_code == 200
    release_argv = [a for a in _argv(rm) if "release" in a][0]
    assert release_argv[release_argv.index("--repo") + 1] == "Tools"
    reg = {"session": "codex-1", "repo": "Games", "issue": 12, "branch": "feat/x"}
    assert client.post("/api/coordination/presence", json=reg, headers=_BOT).status_code == 200