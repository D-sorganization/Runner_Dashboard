"""Claim-aware staff admission (#2562): foreign ``claim:*`` labels and closing PRs refuse a dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from identity import Principal
from staff import claim_guard
from staff.audit import get_audit_store, reset_audit_store
from staff.dispatch_service import DispatchCommand
from staff.store import RunRecord, RunStore

REPO = "D-sorganization/Runner_Dashboard"
OPERATOR = Principal(id="op", type="human", name="Op", roles=["operator"])
BOT = Principal(id="bot", type="bot", name="Bot", roles=["bot"])


def _issue(*labels: str) -> dict[str, Any]:
    return {"number": 7, "labels": [{"name": n} for n in labels]}


def _fake_get(issue: dict[str, Any] | Exception, pulls: list[dict[str, Any]] | Exception | None = None) -> Any:
    calls: list[str] = []

    async def get(endpoint: str) -> Any:
        calls.append(endpoint)
        payload = pulls if "/pulls" in endpoint else issue
        if isinstance(payload, Exception):
            raise payload
        return [] if payload is None else payload

    get.calls = calls  # type: ignore[attr-defined]
    return get


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    monkeypatch.setenv("STAFF_EXCLUSIONS_FILE", str(tmp_path / "excl.json"))
    monkeypatch.delenv("STAFF_CLAIM_GUARD", raising=False)
    monkeypatch.delenv("STAFF_CLAIM_LABEL_APPLY", raising=False)
    reset_audit_store()
    claim_guard.reset_applied()
    yield
    reset_audit_store()
    claim_guard.reset_applied()


# ── check_claim ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claimed_by_codex_is_refused() -> None:
    v = await claim_guard.check_claim(
        REPO, issue=7, role="issue-remediator", get=_fake_get(_issue("claim:codex", "bug"))
    )
    assert v.status == "blocked" and v.code == "claimed_elsewhere"
    assert v.claim_labels == ["claim:codex"]


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["claim:staff", "claim:issue-remediator"])
async def test_own_claim_is_allowed(label: str) -> None:
    v = await claim_guard.check_claim(REPO, issue=7, role="issue-remediator", get=_fake_get(_issue(label)))
    assert v.status == "clear"


@pytest.mark.asyncio
async def test_other_roles_claim_label_is_foreign() -> None:
    v = await claim_guard.check_claim(
        REPO, issue=7, role="issue-remediator", get=_fake_get(_issue("claim:pr-remediator"))
    )
    # pr-remediator is also an RD role label, so it is RD's own claim, not another actor's.
    assert v.status == "clear"


@pytest.mark.asyncio
async def test_open_closing_pr_is_refused() -> None:
    pulls = [{"number": 99, "title": "x", "body": "Closes #7", "html_url": "http://pr/99", "head": {"ref": "a"}}]
    v = await claim_guard.check_claim(REPO, issue=7, role="issue-remediator", get=_fake_get(_issue(), pulls))
    assert v.status == "blocked" and v.code == "closing_pr_exists" and v.pr == 99


@pytest.mark.asyncio
async def test_mention_without_closing_keyword_is_not_a_claim() -> None:
    pulls = [{"number": 99, "title": "see #7", "body": "related to #7, not #70 Closes #70", "head": {"ref": "b"}}]
    v = await claim_guard.check_claim(REPO, issue=7, role="issue-remediator", get=_fake_get(_issue(), pulls))
    assert v.status == "clear"


@pytest.mark.asyncio
async def test_pr_target_checks_labels_only() -> None:
    get = _fake_get(_issue("claim:cursor-agent"))
    v = await claim_guard.check_claim(REPO, pr=12, role="pr-remediator", get=get)
    assert v.status == "blocked" and not any("/pulls" in c for c in get.calls)


@pytest.mark.asyncio
async def test_github_failure_is_unavailable_not_clear() -> None:
    v = await claim_guard.check_claim(REPO, issue=7, role="r", get=_fake_get(RuntimeError("boom")))
    assert v.status == "unavailable" and "boom" in v.message


@pytest.mark.asyncio
async def test_skip_labels_refuse_dispatch() -> None:
    v = await claim_guard.check_claim(REPO, issue=7, role="issue-remediator", get=_fake_get(_issue("do-not-automate")))
    assert v.status == "blocked" and v.code == "skip_label"


def test_issue_attempt_blocker_enforces_no_change_cooldown(tmp_path: Any) -> None:
    now = datetime.now(UTC)
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run(
        RunRecord(
            id="run-old",
            role="ad-hoc",
            provider="claude",
            model=None,
            machine="Desk",
            repo="Runner_Dashboard",
            target_kind="issue",
            target_ref="#7",
            prompt="x",
            status="succeeded",
            created_at=(now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            ended_at=(now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            verification="no_change",
        )
    )
    verdict = claim_guard.issue_attempt_blocker(store, role="ad-hoc", repo="Runner_Dashboard", issue=7, now=now)
    assert verdict is not None and verdict.code == "no_change_cooldown"


def test_issue_attempt_blocker_enforces_attempt_cap(tmp_path: Any) -> None:
    now = datetime.now(UTC)
    store = RunStore(tmp_path / "runs.sqlite3")
    for idx in range(3):
        when = now - timedelta(hours=idx + 1)
        store.create_run(
            RunRecord(
                id=f"run-{idx}",
                role="ad-hoc",
                provider="claude",
                model=None,
                machine="Desk",
                repo="Runner_Dashboard",
                target_kind="issue",
                target_ref="#9",
                prompt="x",
                status="failed",
                created_at=when.isoformat().replace("+00:00", "Z"),
                ended_at=(when + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
            )
        )
    verdict = claim_guard.issue_attempt_blocker(store, role="ad-hoc", repo="Runner_Dashboard", issue=9, now=now)
    assert verdict is not None and verdict.code == "attempt_cap_reached"


# ── manual dispatch enforcement ──────────────────────────────────────────────


def _cmd(**kw: Any) -> DispatchCommand:
    return DispatchCommand(role="issue-remediator", requested_by="op", repo=REPO, issue=7, **kw)


@pytest.mark.asyncio
async def test_dispatch_refused_with_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claim_guard, "_gh_get", _fake_get(_issue("claim:codex")))
    with pytest.raises(HTTPException) as exc:
        await claim_guard.enforce_claim_guard(_cmd(), OPERATOR)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "claimed_elsewhere"
    assert "force_claim_override" in exc.value.detail["message"]


@pytest.mark.asyncio
async def test_guard_flag_off_skips_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_CLAIM_GUARD", "0")
    monkeypatch.setattr(claim_guard, "_gh_get", _fake_get(RuntimeError("must not be called")))
    assert await claim_guard.enforce_claim_guard(_cmd(), OPERATOR) == {}


@pytest.mark.asyncio
async def test_override_requires_operator_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claim_guard, "_gh_get", _fake_get(_issue("claim:codex")))
    with pytest.raises(HTTPException) as exc:
        await claim_guard.enforce_claim_guard(_cmd(force_claim_override=True, claim_override_reason="x"), BOT)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_override_requires_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claim_guard, "_gh_get", _fake_get(_issue("claim:codex")))
    with pytest.raises(HTTPException) as exc:
        await claim_guard.enforce_claim_guard(_cmd(force_claim_override=True, claim_override_reason="  "), OPERATOR)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_override_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claim_guard, "_gh_get", _fake_get(_issue("claim:codex")))
    out = await claim_guard.enforce_claim_guard(
        _cmd(force_claim_override=True, claim_override_reason="codex session is dead"), OPERATOR
    )
    assert out["claim_check"] == "overridden"
    rows = get_audit_store().list_entries(action="dispatch.claim_override")
    assert len(rows) == 1
    detail = rows[0]["detail"] if isinstance(rows[0], dict) else rows[0].detail
    assert "codex session is dead" in str(detail) and "claim:codex" in str(detail)


@pytest.mark.asyncio
async def test_github_failure_is_surfaced_for_manual_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claim_guard, "_gh_get", _fake_get(RuntimeError("down")))
    out = await claim_guard.enforce_claim_guard(_cmd(), OPERATOR)
    assert out["claim_check"] == "unavailable" and "down" in out["claim_warning"]


# ── label apply / release ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_label_applied_for_issue_run() -> None:
    writes: list[tuple[str, str, Any]] = []

    async def write(endpoint: str, method: str = "POST", json_body: Any = None) -> Any:
        writes.append((endpoint, method, json_body))
        return {}

    label = await claim_guard.apply_claim("run-1", REPO, 7, "issue-remediator", write=write)
    assert label == "claim:issue-remediator"
    assert writes == [(f"/repos/{REPO}/issues/7/labels", "POST", {"labels": ["claim:issue-remediator"]})]


@pytest.mark.asyncio
async def test_apply_flag_off_and_failure_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("403")

    assert await claim_guard.apply_claim("run-1", REPO, 7, "issue-remediator", write=boom) is None
    monkeypatch.setenv("STAFF_CLAIM_LABEL_APPLY", "0")
    assert await claim_guard.apply_claim("run-2", REPO, 7, "issue-remediator", write=boom) is None


def test_label_removed_on_terminal_state() -> None:
    claim_guard._applied["run-1"] = (REPO, 7, "claim:issue-remediator")
    calls: list[tuple[str, str]] = []
    claim_guard.release_claim(
        "run-1", "succeeded", write=lambda ep, m: calls.append((ep, m)), others_active=lambda *_: False
    )
    assert calls == [(f"/repos/{REPO}/issues/7/labels/claim%3Aissue-remediator", "DELETE")]
    assert "run-1" not in claim_guard._applied


def test_label_removed_when_lease_blocks_run() -> None:
    claim_guard._applied["run-blocked"] = (REPO, 7, "claim:issue-remediator")
    calls: list[tuple[str, str]] = []
    claim_guard.release_claim(
        "run-blocked", "blocked", write=lambda ep, method: calls.append((ep, method)), others_active=lambda *_: False
    )
    assert calls == [(f"/repos/{REPO}/issues/7/labels/claim%3Aissue-remediator", "DELETE")]
    assert "run-blocked" not in claim_guard._applied


def test_label_not_removed_while_non_terminal_or_shared() -> None:
    claim_guard._applied["run-1"] = (REPO, 7, "claim:staff")
    calls: list[Any] = []
    claim_guard.release_claim("run-1", "running", write=lambda *a: calls.append(a), others_active=lambda *_: False)
    claim_guard.release_claim("run-1", "failed", write=lambda *a: calls.append(a), others_active=lambda *_: True)
    assert calls == []


# ── scheduler ────────────────────────────────────────────────────────────────


def test_scheduler_gate_skips_claimed_target() -> None:
    reason = claim_guard.scheduler_blocker(
        REPO, 7, None, "issue-remediator", fetch=lambda ep: _sync(ep, _issue("claim:codex"))
    )
    assert reason and "claimed_elsewhere" in reason


def test_scheduler_gate_fails_closed_on_github_error() -> None:
    def boom(ep: str) -> Any:
        raise RuntimeError("offline")

    reason = claim_guard.scheduler_blocker(REPO, 7, None, "issue-remediator", fetch=boom)
    assert reason and "claim_check_unavailable" in reason


def test_scheduler_gate_clear_and_untargeted() -> None:
    assert claim_guard.scheduler_blocker(REPO, 7, None, "r", fetch=lambda ep: _sync(ep, _issue())) == ""
    assert claim_guard.scheduler_blocker(REPO, None, None, "r", fetch=lambda ep: 1 / 0) == ""


def test_scheduler_gate_refuses_skip_label_even_without_claim() -> None:
    reason = claim_guard.scheduler_blocker(
        REPO, 7, None, "issue-remediator", fetch=lambda ep: _sync(ep, _issue("do-not-automate"))
    )
    assert "skip_label" in reason


def _sync(endpoint: str, issue: dict[str, Any]) -> Any:
    return [] if "/pulls" in endpoint else issue


# ── /api/staff/{role}/run integration ────────────────────────────────────────

from tests.api.test_staff_fleet import client, staff  # noqa: E402,F401,F811  (shared /run fixtures)


def _stub_github(monkeypatch: pytest.MonkeyPatch, issue: dict[str, Any], writes: list[Any]) -> None:
    async def write(endpoint: str, method: str = "POST", json_body: Any = None) -> Any:
        writes.append((endpoint, method, json_body))
        return {}

    async def no_premise(*a: Any, **k: Any) -> Any:
        return type("P", (), {"allowed": True})()

    monkeypatch.setattr(claim_guard, "_gh_get", _fake_get(issue))
    monkeypatch.setattr(claim_guard, "_gh_write", write)
    monkeypatch.setattr("dispatch_premise.evaluate_dispatch_premise", no_premise)

    async def no_dup(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", no_dup)


def test_route_refuses_claimed_issue_with_409(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    writes: list[Any] = []
    _stub_github(monkeypatch, _issue("claim:codex"), writes)
    resp = client.post("/api/staff/ad-hoc/run", json={"repo": "Runner_Dashboard", "issue": 7, "prompt": "fix"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "claimed_elsewhere"
    assert writes == []


def test_route_override_admits_and_labels_issue(client: Any, staff: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    writes: list[Any] = []
    _stub_github(monkeypatch, _issue("claim:codex"), writes)
    resp = client.post(
        "/api/staff/ad-hoc/run",
        json={
            "repo": "Runner_Dashboard",
            "issue": 7,
            "prompt": "fix",
            "force_claim_override": True,
            "claim_override_reason": "codex session dead",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["claim_check"] == "overridden"
    assert writes == [(f"/repos/{REPO}/issues/7/labels", "POST", {"labels": ["claim:staff"]})]
    run_id = resp.json()["run"]["id"]
    assert any(e["kind"] == "claim_label" for e in staff.store.events_after(run_id))
    assert get_audit_store().list_entries(action="dispatch.claim_override")


def test_route_own_claim_admits_without_override(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    writes: list[Any] = []
    _stub_github(monkeypatch, _issue("claim:staff"), writes)
    resp = client.post("/api/staff/ad-hoc/run", json={"repo": "Runner_Dashboard", "issue": 7, "prompt": "fix"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["claim_check"] == "clear"


def test_route_refuses_recent_no_change_issue(client: Any, staff: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    writes: list[Any] = []
    _stub_github(monkeypatch, _issue("bug"), writes)
    now = datetime.now(UTC)
    staff.store.create_run(
        RunRecord(
            id="run-prev",
            role="ad-hoc",
            provider="claude",
            model=None,
            machine="Desk",
            repo="Runner_Dashboard",
            target_kind="issue",
            target_ref="#7",
            prompt="previous",
            status="succeeded",
            created_at=(now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            ended_at=(now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            verification="no_change",
        )
    )
    resp = client.post("/api/staff/ad-hoc/run", json={"repo": "Runner_Dashboard", "issue": 7, "prompt": "retry"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "no_change_cooldown"


def test_scheduler_tick_records_claim_skip(sched: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from tests.api.test_staff_schedule import _la

    s, runner, clock = sched
    s.tick()
    monkeypatch.setattr(claim_guard, "scheduler_blocker", lambda *a, **k: "claimed_elsewhere: x#7 carries claim:codex")
    clock["now"] = _la(2026, 9, 22, 22, 0, 10)
    decisions = s.tick()
    assert decisions[0]["fired"] is False and "claimed_elsewhere" in decisions[0]["reason"]
    assert runner.submitted == []
    assert "claimed_elsewhere" in s._load_state()["night-watch"]["last_reason"]  # noqa: SLF001


from tests.api.test_staff_schedule import sched  # noqa: E402,F401,F811
