"""Contract tests for Private Mode PM-12 separation of duties (issue #3263, epic #3249)."""

from __future__ import annotations

import datetime as _dt_mod
import sys
from pathlib import Path
from typing import Any

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_context_audit as pca  # noqa: E402
import private_mode as pm  # noqa: E402

TARGET = "example-corp/widgets-sensitive#12"


def _enabled(default: pm.Level = pm.Level.INTERNAL) -> pm.PrivateModeConfig:
    return pm.PrivateModeConfig(enabled=True, default_level=default)


def _store(tmp_path: Path) -> pca.PrivateContextAuditStore:
    return pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3", checkpoint_path=tmp_path / "audit.checkpoint.json")


def _issue(store: Any, cfg: pm.PrivateModeConfig, **kw: Any) -> Any:
    defaults: dict[str, Any] = {
        "kind": "declassify",
        "requester": "operator-alice",
        "approver": "security-bob",
        "target_id": TARGET,
        "destination": "INTERNAL",
        "expires_at": "2026-10-29T00:00:00Z",
        "policy_version": "v1",
        "target_level": pm.Level.INTERNAL,
        "config": cfg,
        "audit_store": store,
    }
    defaults.update(kw)
    return pm.issue_approval(**defaults)


def test_pm12_approval_record_is_immutable_and_self_approval_refused(tmp_path: Path) -> None:
    store, enabled = _store(tmp_path), _enabled()
    with pytest.raises(pm.ClassificationError, match="self-approval"):
        pm.SeparationApproval(
            "declassify", "alice", "alice", TARGET, "INTERNAL", "2026-10-29T00:00:00Z", "v1", pm.Level.INTERNAL
        )
    with pytest.raises(pm.ClassificationError, match="self-approval"):
        _issue(store, enabled, kind="cloud_dispatch", requester="Operator-Alice", approver=" operator-alice ")
    approval = _issue(store, enabled)
    with pytest.raises(AttributeError):
        approval.approver = "someone-else"  # type: ignore[misc]
    assert [d.outcome for d in store.list_decisions()] == ["refused-self-approval", "approval-issued"]


def test_pm12_expired_or_mismatched_approval_is_refused_and_audited(tmp_path: Path) -> None:
    store, enabled = _store(tmp_path), _enabled()
    env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="run", config=enabled)
    approval = _issue(store, enabled, expires_at="2026-10-28T12:00:00Z")
    cases = (
        ("expired", pm.Level.INTERNAL, TARGET, "v1", "2026-10-28T12:00:00Z"),
        ("scope", pm.Level.INTERNAL, "example-corp/widgets-sensitive#99", "v1", "2026-10-28T11:00:00Z"),
        ("destination", pm.Level.PUBLIC, TARGET, "v1", "2026-10-28T11:00:00Z"),
        ("policy_version", pm.Level.INTERNAL, TARGET, "v2", "2026-10-28T11:00:00Z"),
    )
    for match, level, target_id, policy_ver, ref_time in cases:
        with pytest.raises(pm.ClassificationError, match=match):
            pm.declassify_envelope(
                env,
                target_level=level,
                requester="operator-alice",
                target_id=target_id,
                policy_version=policy_ver,
                approval=approval,
                reference_time=ref_time,
                config=enabled,
                audit_store=store,
            )
    assert [d.outcome for d in store.list_decisions()] == [
        "approval-issued",
        "refused-expired",
        "refused-scope-mismatch",
        "refused-destination-mismatch",
        "refused-policy-version-mismatch",
    ]
    assert store.verify_chain().ok is True


def test_pm12_valid_declassify_and_cloud_approval_succeed_and_audit(tmp_path: Path) -> None:
    store, enabled = _store(tmp_path), _enabled()
    env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="run", config=enabled)
    declass_approval = _issue(store, enabled)
    downgraded = pm.declassify_envelope(
        env,
        target_level=pm.Level.INTERNAL,
        requester="operator-alice",
        target_id=TARGET,
        policy_version="v1",
        approval=declass_approval,
        reference_time="2026-10-28T12:00:00Z",
        config=enabled,
        audit_store=store,
    )
    assert (downgraded.level, downgraded.epoch) == (pm.Level.INTERNAL, env.epoch + 1)
    assert downgraded.hops == ("run", "declassify")

    cloud_approval = _issue(store, enabled, kind="cloud_dispatch", destination="cloud:example-provider")
    assert pm.authorize_cloud_dispatch(
        requester="operator-alice",
        target_id=TARGET,
        destination="cloud:example-provider",
        policy_version="v1",
        level=pm.Level.SENSITIVE,
        approval=cloud_approval,
        reference_time="2026-10-28T12:00:00Z",
        config=enabled,
        audit_store=store,
    )
    assert [d.outcome for d in store.list_decisions()] == ["approval-issued", "approved", "approval-issued", "approved"]
    assert store.verify_chain().ok is True


def test_pm12_flag_off_behaviour_unchanged() -> None:
    env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="run", config=pm.DISABLED)
    declassified = pm.declassify_envelope(
        env, target_level=pm.Level.PUBLIC, requester="alice", target_id=TARGET, policy_version="v1", config=pm.DISABLED
    )
    assert declassified.level is pm.Level.PUBLIC
    assert pm.authorize_cloud_dispatch(
        requester="alice", target_id=TARGET, destination="cloud:example", policy_version="v1", config=pm.DISABLED
    )
