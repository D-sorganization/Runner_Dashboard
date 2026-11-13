from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from coordination_fake_rm import FakeRM, bot, claim_status, install, lease_result
from fastapi.testclient import TestClient

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}
_CLAIM = {"repo": "Tools", "issue": 100, "agent": "claude", "session": "claude-1", "intent": "implement"}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-claude")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


@pytest.mark.unit
def test_claim_blocked_when_agent_owns_aging_pr(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm.respond("check_agent_claim", claim_status(False))

    def fetch(path: str) -> list[dict]:
        if path.endswith("/pulls?state=open") or path.endswith("/pulls?state=open&per_page=100"):
            return [
                {
                    "number": 55,
                    "html_url": "https://github.com/D-sorganization/Tools/pull/55",
                    "created_at": "2026-10-01T00:00:00Z",
                    "head": {"ref": "claude/stale-lane"},
                    "labels": [],
                }
            ]
        return []

    import coordination.claims as claims_mod

    monkeypatch.setattr(claims_mod, "evidence_fetch", fetch)
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)

    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["code"] == "aging_pr_blocks_claim"
    assert "post_agent_lease" not in [name for name, _ in rm.calls()]


def _stale_pr_in(repo: str, files: list[str]):
    """Evidence stub: one aging claude PR in ``repo`` that changes ``files`` (#3892)."""

    def fetch(path: str) -> list[dict]:
        if path in (
            f"/repos/D-sorganization/{repo}/pulls?state=open&per_page=100",
            f"/repos/D-sorganization/{repo}/pulls?state=open",
        ):
            return [
                {
                    "number": 87,
                    "html_url": f"https://github.com/D-sorganization/{repo}/pull/87",
                    "created_at": "2026-10-01T00:00:00Z",
                    "head": {"ref": "claude/held-draft"},
                    "labels": [],
                    "draft": True,
                }
            ]
        if path in (
            f"/repos/D-sorganization/{repo}/pulls/87/files?per_page=100",
            f"/repos/D-sorganization/{repo}/pulls/87/files",
        ):
            return [{"filename": name} for name in files]
        return []

    return fetch


@pytest.mark.unit
def test_aging_pr_in_another_repo_does_not_block_claim(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(agent="claude"))
    import coordination.claims as claims_mod

    monkeypatch.setattr(claims_mod, "evidence_fetch", _stale_pr_in("Operations_Manual_and_Training", ["HANDOFF.md"]))
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)

    assert resp.status_code == 200, resp.text
    assert "post_agent_lease" in [name for name, _ in rm.calls()]


@pytest.mark.unit
def test_same_repo_aging_pr_with_disjoint_claim_paths_does_not_block(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(agent="claude"))
    import coordination.claims as claims_mod

    monkeypatch.setattr(claims_mod, "evidence_fetch", _stale_pr_in("Tools", ["docs/README.md"]))
    resp = client.post("/api/coordination/claims", json={**_CLAIM, "paths": ["src/movement_optimizer"]}, headers=_BOT)

    assert resp.status_code == 200, resp.text


@pytest.mark.unit
def test_same_repo_aging_pr_with_overlapping_claim_paths_blocks(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    import coordination.claims as claims_mod

    monkeypatch.setattr(claims_mod, "evidence_fetch", _stale_pr_in("Tools", ["src/movement_optimizer/core.py"]))
    resp = client.post("/api/coordination/claims", json={**_CLAIM, "paths": ["src/movement_optimizer"]}, headers=_BOT)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "aging_pr_blocks_claim"
    assert "src/movement_optimizer/core.py" in detail["message"]
