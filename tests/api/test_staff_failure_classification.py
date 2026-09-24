"""API integration tests for staff run failure classification (SC-A6, Issue #1297)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from identity import Principal, TokenRecord, identity_manager
from server import app
from staff import store as store_mod
from staff.store import RunRecord, reset_store

_REMOTE = ("100.64.0.15", 52000)
_XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def temp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[store_mod.RunStore]:
    db = tmp_path / "test_staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_store()
    yield store_mod.get_store()
    reset_store()


@pytest.fixture
def operator_token() -> Iterator[str]:
    raw_token = "rd_operator_classification_test_token"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    rec = TokenRecord(
        token_hash=token_hash,
        principal_id="test-operator",
        name="Operator Token",
        created_at=time.time(),
    )
    principal = Principal(
        id="test-operator",
        type="user",
        name="Operator",
        roles=["operator"],
    )
    identity_manager.principals["test-operator"] = principal
    identity_manager.tokens.append(rec)
    yield raw_token
    identity_manager.tokens = [t for t in identity_manager.tokens if t.token_hash != token_hash]
    identity_manager.principals.pop("test-operator", None)


@pytest.fixture
def authed_client(operator_token: str) -> TestClient:
    return TestClient(
        app,
        headers={"Authorization": f"Bearer {operator_token}", **_XHR},
        client=_REMOTE,
    )


def test_run_detail_exposes_failure_classification(
    temp_store: store_mod.RunStore,
    authed_client: TestClient,
) -> None:
    """GET /api/staff/runs/{id} returns failure_class, retryable, and remediation."""
    run = RunRecord(
        id="run-auth-test",
        role="coder",
        provider="claude",
        model="sonnet",
        machine="OGLaptop",
        repo="Tools",
        target_kind="issue",
        target_ref="#101",
        prompt="fix bug",
        status="failed",
        failure_class="auth_expired",
        retryable=False,
        remediation="Run `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` on node OGLaptop.",
        error="401 OAuth access token has expired",
    )
    temp_store.create_run(run)

    resp = authed_client.get(f"/api/staff/runs/{run.id}")
    assert resp.status_code == 200
    data = resp.json()["run"]
    assert data["id"] == "run-auth-test"
    assert data["failure_class"] == "auth_expired"
    assert data["retryable"] is False
    assert "CLAUDE_CONFIG_DIR" in data["remediation"]
    assert data["error"] == "401 OAuth access token has expired"


def test_summary_attention_exposes_classified_failures_and_deduplicates_auth(
    temp_store: store_mod.RunStore,
    authed_client: TestClient,
) -> None:
    """GET /api/staff/summary deduplicates auth_expired runs per node+provider and exposes remediation."""
    # 2 Claude auth failures on OGLaptop
    temp_store.create_run(
        RunRecord(
            id="run-c1",
            role="coder",
            provider="claude",
            model="sonnet",
            machine="OGLaptop",
            repo="Tools",
            target_kind="issue",
            target_ref="#101",
            prompt="task 1",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="401 Unauthorized",
            remediation="Run `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` on node OGLaptop.",
        )
    )
    temp_store.create_run(
        RunRecord(
            id="run-c2",
            role="coder",
            provider="claude",
            model="sonnet",
            machine="OGLaptop",
            repo="Tools",
            target_kind="issue",
            target_ref="#102",
            prompt="task 2",
            status="failed",
            failure_class="auth_expired",
            retryable=False,
            error="401 Unauthorized",
            remediation="Run `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` on node OGLaptop.",
        )
    )
    # 1 Rate limit failure on DeskComputer
    temp_store.create_run(
        RunRecord(
            id="run-rl",
            role="worker",
            provider="codex",
            model=None,
            machine="DeskComputer",
            repo="Tools",
            target_kind="issue",
            target_ref="#103",
            prompt="task 3",
            status="failed",
            failure_class="rate_limited",
            retryable=True,
            error="429 Rate limit exceeded",
            remediation="Provider codex rate limit reached; back off before retry.",
        )
    )

    resp = authed_client.get("/api/staff/summary")
    assert resp.status_code == 200
    attention = resp.json()["attention"]

    # Should have 2 attention items: 1 deduplicated for Claude auth on OGLaptop, 1 for rate limit
    assert len(attention) == 2

    auth_item = next((item for item in attention if item.get("failure_class") == "auth_expired"), None)
    assert auth_item is not None
    assert auth_item["machine"] == "OGLaptop"
    assert auth_item["provider"] == "claude"
    assert "CLAUDE_CONFIG_DIR" in auth_item["remediation"]
    assert set(auth_item.get("affected_runs", [])) == {"run-c1", "run-c2"}

    rl_item = next((item for item in attention if item.get("failure_class") == "rate_limited"), None)
    assert rl_item is not None
    assert rl_item["id"] == "run-rl"
    assert rl_item["retryable"] is True
    assert "rate limit" in rl_item["remediation"].lower()


def test_schema_migration_preserves_existing_data(tmp_path: Path) -> None:
    """RunStore schema migration idempotently adds retryable and remediation columns."""
    import sqlite3

    db_path = tmp_path / "legacy.sqlite3"
    # Create legacy table without retryable and remediation
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            machine TEXT NOT NULL,
            repo TEXT NOT NULL DEFAULT '',
            target_kind TEXT NOT NULL,
            target_ref TEXT NOT NULL DEFAULT '',
            prompt TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            exit_code INTEGER,
            cost_usd REAL NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            workdir TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT '',
            transcript_path TEXT NOT NULL DEFAULT '',
            lease_id TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            last_line TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        INSERT INTO runs (id, role, provider, machine, target_kind, prompt, status, created_at)
        VALUES ('legacy-run', 'coder', 'claude', 'node1', 'issue', 'test', 'succeeded', '2026-09-24T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()

    # Open with RunStore which executes _migrate()
    store = store_mod.RunStore(db_path)
    cols = store.columns()
    assert "retryable" in cols
    assert "remediation" in cols
    assert "failure_class" in cols

    rec = store.get_run("legacy-run")
    assert rec is not None
    assert rec.retryable is False
    assert rec.remediation == ""
    assert rec.failure_class == ""
    store.close()
