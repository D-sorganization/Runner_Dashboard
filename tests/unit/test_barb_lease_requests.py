"""Barb lease requests over rd-threads (#3301)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from staff.barb_continuity import DEFAULT_LEASE_TTL_SECONDS, BarbContinuityStore
from staff.barb_lease_requests import apply_request, build_request, remote_holders_from_env

from scripts import rd_threads_envelope as rd_env

SECRET = "ghp_" + ("x" * 40)  # pragma: allowlist secret
HOLDER, SCOPE = "claude-barb-cloud", "barb:front-door"
NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> BarbContinuityStore:
    return BarbContinuityStore(tmp_path / "leases.sqlite3")


def _req(action: str, **fields: object) -> dict:
    return build_request(action, SCOPE, sender=HOLDER, now=1_700_000_000.0, **fields)


def test_build_and_checkout_flow(tmp_path: Path) -> None:
    env = _req("status")
    assert env["kind"] == "update" and rd_env.validate(env) == []
    store, allow = _store(tmp_path), frozenset({HOLDER})

    out, ans = apply_request(store, _req("checkout", summary="night watch"), allow, now=NOW)
    assert out["ok"] and out["epoch"] == 1 and ans["kind"] == "answer" and rd_env.validate(ans) == []

    out2, _ = apply_request(store, _req("checkout"), allow, now=NOW + timedelta(minutes=5))
    assert out2["epoch"] == 1

    assert apply_request(store, _req("heartbeat", epoch=1), allow, now=NOW + timedelta(minutes=10))[0]["ok"]
    rel_no_to, _ = apply_request(store, _req("release", epoch=1), allow, now=NOW + timedelta(minutes=11))
    assert rel_no_to["error"] == "turnover_required"
    to = {"summary": "Handoff complete", "next_actions": ["Monitor queue"]}
    assert apply_request(store, _req("release", epoch=1, turnover=to), allow, now=NOW + timedelta(minutes=12))[0]["ok"]
    st, _ = apply_request(store, _req("status"), allow, now=NOW + timedelta(minutes=13))
    assert st["ok"]


def test_conflict_expiry_and_stale_epoch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    allow = frozenset({HOLDER, "codex-barb-cloud"})
    apply_request(store, _req("checkout"), allow, now=NOW)
    blocked = apply_request(
        store,
        build_request("checkout", SCOPE, sender="codex-barb-cloud", now=1_700_000_100.0),
        allow,
        now=NOW + timedelta(minutes=1),
    )[0]
    assert blocked["error"] == "lease_conflict" and blocked["holder"] == HOLDER

    apply_request(store, _req("checkout", ttl_seconds=120), frozenset({HOLDER}), now=NOW)
    later = NOW + timedelta(seconds=DEFAULT_LEASE_TTL_SECONDS + 120)
    out = apply_request(
        store,
        build_request("checkout", SCOPE, sender="codex-barb-cloud", now=1_700_000_200.0),
        frozenset({"codex-barb-cloud"}),
        now=later,
    )[0]
    assert out["epoch"] == 2

    store2 = _store(tmp_path / "stale")
    apply_request(store2, _req("checkout", ttl_seconds=120), frozenset({HOLDER}), now=NOW)
    apply_request(store2, _req("checkout", ttl_seconds=120), frozenset({HOLDER}), now=NOW + timedelta(minutes=3))
    mid = NOW + timedelta(minutes=4)
    assert apply_request(store2, _req("heartbeat", epoch=1), frozenset({HOLDER}), now=mid)[0]["error"] == "stale_epoch"
    to = {"summary": "Too late", "next_actions": []}
    rel_stale, _ = apply_request(store2, _req("release", epoch=1, turnover=to), frozenset({HOLDER}), now=mid)
    assert rel_stale["error"] == "stale_epoch"


@pytest.mark.parametrize(
    ("env", "error"),
    [
        (build_request("checkout", SCOPE, sender="stranger", now=1_700_000_000.0), "sender_not_allowed"),
        (
            build_request("checkout", SCOPE, sender=HOLDER, holder="other-seat", now=1_700_000_000.0),
            "holder_sender_mismatch",
        ),
        (_req("checkout", force=True), "force_forbidden"),
        (build_request("checkout", SCOPE, sender=HOLDER, summary=SECRET, now=1_700_000_000.0), "secret_forbidden"),
    ],
)
def test_refusals_never_write(tmp_path: Path, env: dict, error: str) -> None:
    store = _store(tmp_path)
    out, _ = apply_request(store, env, frozenset({HOLDER}), now=NOW)
    assert out["ok"] is False and out["error"] == error
    assert store.list_leases(now=NOW) == []


def test_dry_run_and_allowlist_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    out, _ = apply_request(store, _req("checkout"), frozenset({HOLDER}), now=NOW, dry_run=True)
    assert out["dry_run"] and store.list_leases(now=NOW) == []
    monkeypatch.setenv("BARB_REMOTE_HOLDERS", " a , b ")
    assert remote_holders_from_env() == frozenset({"a", "b"})
