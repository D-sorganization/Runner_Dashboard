"""RD-1 CI-fix service and webhook route: intake, merge-queue handling and launch (#1881, #1879).

Covers:
- ``X-Hub-Signature-256`` verification (fail closed without a secret), replayed deliveries,
  and the ``CI_FIX_DISPATCH_ENABLED`` flag (default off).
- Parsing of ``workflow_run`` failures, including ``merge_group`` runs whose
  ``pull_requests`` is empty (PR number recovered from ``gh-readonly-queue/<base>/pr-<N>-<sha>``),
  and ``pull_request`` ``dequeued`` with ``MERGE_CONFLICT`` / ``CHECKS_FAILED``.
- One dispatch per PR: a merge-group failure and its dequeue event yield exactly one launch.
- ``/dispatch`` launches through the staff dispatch path, or returns 501 when the routed
  provider is not installed on this node.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import ci_fix_dispatch
import ci_fix_events
import ci_fix_service
import pytest
from ci_fix_dispatch import CIFixLockManager
from ci_fix_events import parse_github_event
from fastapi.testclient import TestClient
from identity import Principal, require_principal
from replay_store import ReplayStore
from routers import remediation_ci_fix

from backend.server import app

SECRET = "test-webhook-secret"
OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"], scopes=["remediation.dispatch"])
WEBHOOK_PATH = "/api/remediation/ci-fix/webhook"


# ─── fixtures ────────────────────────────────────────────────────────────────


class FakeLauncher:
    """Records staff dispatch commands instead of starting a worker."""

    def __init__(self) -> None:
        self.commands: list[Any] = []

    async def __call__(self, cmd: Any, caller: Principal) -> dict[str, Any]:
        self.commands.append(cmd)
        return {"dry_run": False, "run": {"id": f"run-fake{len(self.commands)}", "status": "queued"}}


class FakeGitHub:
    """Stands in for ``gh_client.get`` / ``get_text`` with canned REST responses."""

    def __init__(self, responses: dict[str, Any], texts: dict[str, str] | None = None) -> None:
        self.responses = responses
        self.texts = texts or {}
        self.calls: list[str] = []

    async def get(self, path: str) -> Any:
        self.calls.append(path)
        for prefix, value in self.responses.items():
            if path.startswith(prefix):
                return value
        raise AssertionError(f"unexpected GitHub GET {path}")

    async def get_text(self, path: str) -> str:
        self.calls.append(path)
        for prefix, value in self.texts.items():
            if path.startswith(prefix):
                return value
        raise AssertionError(f"unexpected GitHub text GET {path}")


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch) -> FakeLauncher:
    fake = FakeLauncher()
    monkeypatch.setattr(ci_fix_service, "dispatch_staff_run", fake)
    monkeypatch.setattr(
        ci_fix_service, "available_providers", lambda: dict.fromkeys(("claude", "codex", "antigravity"), True)
    )
    return fake


@pytest.fixture
def locks(monkeypatch: pytest.MonkeyPatch) -> CIFixLockManager:
    mgr = CIFixLockManager()
    monkeypatch.setattr(ci_fix_service, "GLOBAL_CI_FIX_LOCK_MGR", mgr)
    monkeypatch.setattr(remediation_ci_fix, "GLOBAL_CI_FIX_LOCK_MGR", mgr)
    return mgr


@pytest.fixture
def audit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ci_fix_audit.json"
    monkeypatch.setattr(ci_fix_dispatch, "DEFAULT_AUDIT_PATH", path)
    return path


@pytest.fixture
def webhook_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("CI_FIX_DISPATCH_ENABLED", "1")
    monkeypatch.setattr(remediation_ci_fix, "_replay_store", ReplayStore(tmp_path / "replay.db"))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def operator_client() -> TestClient:
    app.dependency_overrides[require_principal] = lambda: OPERATOR
    yield TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    app.dependency_overrides.pop(require_principal, None)


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(
    client: TestClient, event: str, payload: dict[str, Any], *, delivery: str | None = None, sig: str | None = None
):
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or uuid.uuid4().hex,
        "X-Hub-Signature-256": sig if sig is not None else _sign(body),
        "Content-Type": "application/json",
    }
    return client.post(WEBHOOK_PATH, content=body, headers=headers)


REPO = {
    "name": "Runner_Dashboard",
    "full_name": "D-sorganization/Runner_Dashboard",
    "owner": {"login": "D-sorganization"},
}


def _workflow_run(
    *,
    conclusion: str = "failure",
    prs: list[dict[str, Any]] | None = None,
    head_branch: str = "feat/x",
    event: str = "pull_request",
) -> dict[str, Any]:
    return {
        "action": "completed",
        "repository": REPO,
        "workflow_run": {
            "id": 555,
            "name": "CI Standard",
            "event": event,
            "conclusion": conclusion,
            "head_branch": head_branch,
            "pull_requests": [{"number": 42, "head": {"ref": "feat/x"}, "base": {"ref": "main"}}]
            if prs is None
            else prs,
        },
    }


def _dequeued(reason: str, *, draft: bool = False) -> dict[str, Any]:
    return {
        "action": "dequeued",
        "reason": reason,
        "repository": REPO,
        "pull_request": {
            "number": 77,
            "state": "open",
            "draft": draft,
            "auto_merge": None,
            "head": {"ref": "feat/conflicted"},
            "base": {"ref": "main"},
        },
    }


QUEUE_REF = "gh-readonly-queue/main/pr-77-0123456789abcdef0123456789abcdef01234567"


# ─── webhook route ───────────────────────────────────────────────────────────


def test_webhook_fails_closed_without_secret(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    resp = _post(client, "workflow_run", _workflow_run())
    assert resp.status_code == 503


def test_webhook_rejects_bad_signature(client: TestClient, webhook_env: None) -> None:
    resp = _post(client, "workflow_run", _workflow_run(), sig="sha256=" + "0" * 64)
    assert resp.status_code == 401


def test_webhook_flag_off_accepts_nothing(
    client: TestClient, webhook_env: None, launcher: FakeLauncher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI_FIX_DISPATCH_ENABLED", raising=False)
    resp = _post(client, "workflow_run", _workflow_run())
    assert resp.status_code == 202
    assert resp.json()["accepted"] is False
    assert "CI_FIX_DISPATCH_ENABLED" in resp.json()["reason"]
    assert launcher.commands == []


def test_webhook_ping(client: TestClient, webhook_env: None) -> None:
    resp = _post(client, "ping", {"zen": "hi", "repository": REPO})
    assert resp.status_code == 202
    assert resp.json()["accepted"] is False


def test_webhook_workflow_failure_dispatches_once(
    client: TestClient,
    webhook_env: None,
    launcher: FakeLauncher,
    locks: CIFixLockManager,
    audit_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = "\n".join(f"line {i}" for i in range(500)) + "\nFAILED tests/test_a.py::test_b - boom"
    gh = FakeGitHub(
        {
            "/repos/D-sorganization/Runner_Dashboard/pulls/42": {
                "number": 42,
                "state": "open",
                "draft": False,
                "auto_merge": {"merge_method": "squash"},
                "head": {"ref": "feat/x"},
                "base": {"ref": "main"},
            },
            "/repos/D-sorganization/Runner_Dashboard/actions/runs/555/jobs": {
                "jobs": [{"id": 9, "name": "tests", "conclusion": "failure"}]
            },
        },
        {"/repos/D-sorganization/Runner_Dashboard/actions/jobs/9/logs": log},
    )
    monkeypatch.setattr(ci_fix_service, "gh_client", gh)

    delivery = uuid.uuid4().hex
    resp = _post(client, "workflow_run", _workflow_run(), delivery=delivery)
    assert resp.status_code == 202
    assert resp.json()["accepted"] is True

    assert len(launcher.commands) == 1
    cmd = launcher.commands[0]
    assert (cmd.repo, cmd.pr, cmd.role, cmd.machine) == ("Runner_Dashboard", 42, "ad-hoc", "local")
    assert cmd.skip_premise_check is True
    assert "tests/test_a.py::test_b" in cmd.prompt
    assert "line 299" not in cmd.prompt  # ≤ 200-line tail
    assert locks.get_lock_info("Runner_Dashboard", 42)["run_id"] == "run-fake1"

    # A GitHub redelivery of the same event is a replay, and a second failure while the
    # session is active is held off by the per-PR lock.
    assert _post(client, "workflow_run", _workflow_run(), delivery=delivery).json()["accepted"] is False
    _post(client, "workflow_run", _workflow_run())
    assert len(launcher.commands) == 1

    entries = json.loads(audit_file.read_text())
    assert len(entries) == 1
    assert entries[0]["staff_run_id"] == "run-fake1"
    assert "cost_budget" in entries[0] and "cost_estimate" not in entries[0]


def test_webhook_skips_draft_pr(
    client: TestClient,
    webhook_env: None,
    launcher: FakeLauncher,
    locks: CIFixLockManager,
    audit_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = FakeGitHub(
        {
            "/repos/D-sorganization/Runner_Dashboard/pulls/42": {
                "number": 42,
                "state": "open",
                "draft": True,
                "auto_merge": None,
                "head": {"ref": "feat/x"},
                "base": {"ref": "main"},
            }
        }
    )
    monkeypatch.setattr(ci_fix_service, "gh_client", gh)
    _post(client, "workflow_run", _workflow_run())
    assert launcher.commands == []
    assert not locks.is_locked("Runner_Dashboard", 42)


def test_merge_group_failure_and_dequeue_yield_exactly_one_dispatch(
    client: TestClient,
    webhook_env: None,
    launcher: FakeLauncher,
    locks: CIFixLockManager,
    audit_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = FakeGitHub(
        {
            "/repos/D-sorganization/Runner_Dashboard/pulls/77": {
                "number": 77,
                "state": "open",
                "draft": False,
                "auto_merge": None,
                "head": {"ref": "feat/conflicted"},
                "base": {"ref": "main"},
            },
            "/repos/D-sorganization/Runner_Dashboard/actions/runs/555/jobs": {"jobs": []},
            "/repos/D-sorganization/Runner_Dashboard/actions/runs?": {"workflow_runs": []},
        }
    )
    monkeypatch.setattr(ci_fix_service, "gh_client", gh)

    _post(client, "workflow_run", _workflow_run(prs=[], head_branch=QUEUE_REF, event="merge_group"))
    _post(client, "pull_request", _dequeued("CHECKS_FAILED"))

    assert len(launcher.commands) == 1
    assert launcher.commands[0].pr == 77
    assert "merge queue" in launcher.commands[0].prompt.lower()
    assert len(json.loads(audit_file.read_text())) == 1


def test_dequeue_checks_failed_uses_the_merge_group_run_log(
    client: TestClient,
    webhook_env: None,
    launcher: FakeLauncher,
    locks: CIFixLockManager,
    audit_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = FakeGitHub(
        {
            "/repos/D-sorganization/Runner_Dashboard/actions/runs?": {
                "workflow_runs": [
                    {"id": 700, "name": "CI Standard", "head_branch": "gh-readonly-queue/main/pr-12-aaaaaaa"},
                    {"id": 701, "name": "CI Standard", "head_branch": QUEUE_REF},
                ]
            },
            "/repos/D-sorganization/Runner_Dashboard/actions/runs/701/jobs": {
                "jobs": [{"id": 31, "name": "tests", "conclusion": "failure"}]
            },
        },
        {"/repos/D-sorganization/Runner_Dashboard/actions/jobs/31/logs": "FAILED tests/test_q.py::test_queue"},
    )
    monkeypatch.setattr(ci_fix_service, "gh_client", gh)

    _post(client, "pull_request", _dequeued("CHECKS_FAILED"))

    assert len(launcher.commands) == 1
    assert "tests/test_q.py::test_queue" in launcher.commands[0].prompt
    assert json.loads(audit_file.read_text())[0]["run_id"] == 701


def test_dequeue_merge_conflict_dispatches_low_effort_cli_merge_job(
    client: TestClient,
    webhook_env: None,
    launcher: FakeLauncher,
    locks: CIFixLockManager,
    audit_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ci_fix_service, "gh_client", FakeGitHub({}))
    monkeypatch.setattr(ci_fix_dispatch, "_agy_runs_unattended", lambda: False)

    _post(client, "pull_request", _dequeued("MERGE_CONFLICT"))

    assert len(launcher.commands) == 1
    cmd = launcher.commands[0]
    assert cmd.provider == "claude" and cmd.model == "claude-sonnet-5-5"
    prompt = cmd.prompt
    assert "merge `origin/main` into" in prompt.lower()
    assert "keep both rows" in prompt.lower()
    assert "auto-merge" in prompt.lower()
    entry = json.loads(audit_file.read_text())[0]
    assert entry["failure_type"] == "conflict"
    assert entry["effort"] == "low"


def test_dequeue_on_draft_is_skipped(
    client: TestClient,
    webhook_env: None,
    launcher: FakeLauncher,
    locks: CIFixLockManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ci_fix_service, "gh_client", FakeGitHub({}))
    _post(client, "pull_request", _dequeued("MERGE_CONFLICT", draft=True))
    assert launcher.commands == []


def test_event_handler_never_raises(locks: CIFixLockManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """Orthogonality: a GitHub outage inside the CI-fix handler must not escape it."""
    import asyncio

    class Boom:
        async def get(self, path: str) -> Any:
            raise RuntimeError("github down")

    monkeypatch.setattr(ci_fix_service, "gh_client", Boom())
    event, _ = parse_github_event("workflow_run", _workflow_run())
    assert event is not None
    result = asyncio.run(ci_fix_service.handle_ci_fix_event(event))
    assert result["dispatched"] is False


# ─── /dispatch launches ─────────────────────────────────────────────────────


def test_dispatch_endpoint_launches_and_attaches_run(
    operator_client: TestClient, launcher: FakeLauncher, locks: CIFixLockManager, audit_file: Path
) -> None:
    resp = operator_client.post(
        "/api/remediation/ci-fix/dispatch",
        json={"repo": "Runner_Dashboard", "pr_number": 303, "workflow_name": "Lint", "log_tail": "ruff failed"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "dispatched"
    assert data["staff_run_id"] == "run-fake1"
    assert launcher.commands[0].provider == "codex"
    assert locks.get_lock_info("Runner_Dashboard", 303)["run_id"] == "run-fake1"


def test_dispatch_endpoint_501_when_provider_missing(
    operator_client: TestClient,
    launcher: FakeLauncher,
    locks: CIFixLockManager,
    audit_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ci_fix_service, "available_providers", lambda: {"claude": False, "codex": False, "antigravity": False}
    )
    resp = operator_client.post(
        "/api/remediation/ci-fix/dispatch",
        json={"repo": "Runner_Dashboard", "pr_number": 304, "workflow_name": "Lint", "log_tail": "ruff failed"},
    )
    assert resp.status_code == 501
    assert "codex" in resp.json()["detail"]
    assert launcher.commands == []
    assert not locks.is_locked("Runner_Dashboard", 304)  # a failed launch releases the lock
    assert not audit_file.exists()


def test_dispatch_endpoint_validates_body(
    operator_client: TestClient, launcher: FakeLauncher, locks: CIFixLockManager
) -> None:
    resp = operator_client.post("/api/remediation/ci-fix/dispatch", json={"repo": "", "pr_number": 0})
    assert resp.status_code == 422
    assert launcher.commands == []


def test_dispatch_escalates_after_three_recorded_attempts(
    operator_client: TestClient, launcher: FakeLauncher, locks: CIFixLockManager, audit_file: Path
) -> None:
    body = {"repo": "Runner_Dashboard", "pr_number": 305, "workflow_name": "CI Standard", "log_tail": "FAILED t.py::x"}
    tiers = []
    for _ in range(3):
        resp = operator_client.post("/api/remediation/ci-fix/dispatch", json=body)
        assert resp.status_code == 200, resp.text
        tiers.append(resp.json()["route"]["tier"])
        locks.release("Runner_Dashboard", 305)
    assert tiers == ["cli", "cli", "strong"]


def test_webhook_path_is_exempt_from_operator_perimeter_only() -> None:
    from middleware import is_auth_exempt

    assert is_auth_exempt(WEBHOOK_PATH)
    assert not is_auth_exempt("/api/remediation/ci-fix/dispatch")
    assert ci_fix_events.GITHUB_WEBHOOK_SECRET_ENV == "GITHUB_WEBHOOK_SECRET"
