"""Contract tests for Private Mode PM-17 required-check wiring (issue #3268, epic #3249)."""

from __future__ import annotations

import datetime as _dt_mod
import json
import sys
from pathlib import Path

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from private_context_audit import PrivateContextAuditStore  # noqa: E402
from private_mode import ClassificationError, Level, PrivateModeConfig, load_classification_policy  # noqa: E402

from backend.ci import private_required_check as prc  # noqa: E402

PR_SHA, MQ_SHA = "a" * 40, "b" * 40
PM_ON = PrivateModeConfig(enabled=True, default_level=Level.SENSITIVE)


def _write_overlay_policy(tmp_path: Path):
    policy_dir = tmp_path / "overlay"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "classification.json").write_text(
        json.dumps(
            {
                "default_level": "INTERNAL",
                "repos": [
                    {"repo": "example-corp/widgets-private", "level": "SENSITIVE"},
                    {"repo": "example-corp/widgets-public", "level": "PUBLIC"},
                ],
                "path_globs": [],
                "operator_marks": {},
            }
        ),
        encoding="utf-8",
    )
    return load_classification_policy(policy_dir / "classification.json")


def test_pool_down_keeps_check_pending_with_sla_alert_and_never_auto_passes(tmp_path: Path) -> None:
    """Acceptance criterion 1: pool down -> check stays pending with SLA + alert, never auto-passes."""
    alerts: list[dict[str, object]] = []
    policy = _write_overlay_policy(tmp_path)
    wiring = prc.PrivateRequiredCheckWiring(
        overlay_policy=policy, private_mode=PM_ON, sla_seconds=300, alert_sink=alerts.append
    )
    res = wiring.evaluate_check(
        run_id="run001",
        repo="example-corp/widgets-private",
        sha=PR_SHA,
        target_kind="pr_head",
        pool_healthy=False,
        workflows=[{"name": "CI", "jobs": {"test": {"runs-on": "private-pool"}}}],
        enabled=True,
        now="2026-10-28T22:00:00Z",
    )
    assert res.enabled is True and res.state == "pending" and res.auto_passed is False
    assert res.sla_deadline == "2026-10-28T22:05:00Z" and res.alert_emitted is True
    assert len(alerts) == 1 and "widgets-private" not in json.dumps(alerts[0])


def test_hosted_workflow_on_private_repo_fails_closed_via_overlay_policy(tmp_path: Path) -> None:
    """Acceptance criterion 2: a hosted workflow on a private repo fails closed (policy reads overlay only)."""
    policy = _write_overlay_policy(tmp_path)
    wiring = prc.PrivateRequiredCheckWiring(overlay_policy=policy, private_mode=PM_ON)
    hosted_wf = [{"name": "CI", "jobs": {"lint": {"runs-on": "ubuntu-latest"}}}]
    with pytest.raises(ClassificationError, match="hosted runner"):
        wiring.evaluate_check(
            run_id="run002",
            repo="example-corp/widgets-private",
            sha=PR_SHA,
            target_kind="pr_head",
            pool_healthy=True,
            workflows=hosted_wf,
            enabled=True,
        )
    pub_res = wiring.evaluate_check(
        run_id="run003",
        repo="example-corp/widgets-public",
        sha=PR_SHA,
        target_kind="pr_head",
        pool_healthy=True,
        workflows=hosted_wf,
        enabled=True,
        attested_state="success",
    )
    assert pub_res.state == "success" and pub_res.auto_passed is False


def test_manual_override_requires_two_principals_and_binds_to_merge_queue_sha(tmp_path: Path) -> None:
    """Acceptance criterion 3: manual fallback requires 2 distinct principals, is audited, and binds to SHA/target."""
    policy = _write_overlay_policy(tmp_path)
    audit_store = PrivateContextAuditStore(tmp_path / "audit.db")
    wiring = prc.PrivateRequiredCheckWiring(overlay_policy=policy, private_mode=PM_ON, audit_store=audit_store)
    with pytest.raises(ClassificationError, match="self-approval|distinct"):
        prc.issue_manual_fallback(
            requester="alice",
            approver="alice",
            repo="example-corp/widgets-private",
            sha=MQ_SHA,
            target_kind="merge_queue",
            expires_at="2026-10-29T00:00:00Z",
            policy_version="v1",
            config=PM_ON,
            audit_store=audit_store,
        )
    override = prc.issue_manual_fallback(
        requester="alice",
        approver="bob",
        repo="example-corp/widgets-private",
        sha=MQ_SHA,
        target_kind="merge_queue",
        expires_at="2026-10-29T00:00:00Z",
        policy_version="v1",
        config=PM_ON,
        audit_store=audit_store,
    )
    with pytest.raises(ClassificationError, match="mismatch"):
        wiring.apply_manual_fallback(
            override,
            repo="example-corp/widgets-private",
            sha=PR_SHA,
            target_kind="pr_head",
            policy_version="v1",
            now="2026-10-28T23:00:00Z",
        )
    ok_res = wiring.apply_manual_fallback(
        override,
        repo="example-corp/widgets-private",
        sha=MQ_SHA,
        target_kind="merge_queue",
        policy_version="v1",
        now="2026-10-28T23:00:00Z",
    )
    assert ok_res.state == "success" and ok_res.manual_override is True and len(audit_store.list_decisions()) >= 2


def test_flag_off_behaviour_unchanged_and_publish_guard(tmp_path: Path) -> None:
    """Acceptance criteria 4 & 5: flag-off behaviour unchanged and passes #3080 publish guard."""
    policy = _write_overlay_policy(tmp_path)
    wiring = prc.PrivateRequiredCheckWiring(overlay_policy=policy)
    res = wiring.evaluate_check(
        run_id="run004",
        repo="example-corp/widgets-private",
        sha=PR_SHA,
        target_kind="pr_head",
        pool_healthy=False,
        workflows=[{"name": "CI", "jobs": {"lint": {"runs-on": "ubuntu-latest"}}}],
        enabled=False,
    )
    assert res.enabled is False and res.state == "disabled" and res.alert_emitted is False

    sys.path.insert(0, str(_ROOT / "scripts"))
    import publish_guard as pg  # noqa: PLC0415

    paths = ["backend/ci/private_required_check.py", "tests/test_private_required_check.py"]
    assert pg.check(_ROOT, paths, pg.load_rules()) == []
