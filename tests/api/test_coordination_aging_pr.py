from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import identity
import pytest
from coordination_fake_rm import FakeRM, bot, claim_status, install
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
        if path.endswith("/pulls?state=open&per_page=100"):
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
