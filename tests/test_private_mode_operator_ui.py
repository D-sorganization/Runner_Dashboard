"""Tests for Private Mode PM-19 operator UI projections and audit viewer (issue #3270, epic #3249)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
for _p in (str(_BACKEND), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import private_context_audit as pca  # noqa: E402
import private_mode as pm  # noqa: E402
import private_mode_admission as pma  # noqa: E402
import private_mode_emit as pme  # noqa: E402
import private_mode_operator_ui as pmui  # noqa: E402
import private_mode_parking as pmp  # noqa: E402
import private_mode_sinks as pms  # noqa: E402
import publish_guard as pg  # noqa: E402

CANARY = "CANARY-" + "example-corp-pm19-operator-ui-secret"
_REPO = "example-corp/widgets-sensitive"
_TS = "2026-10-28T12:00:00Z"
_CFG = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)


def _encode_canary(canary: str, form: str) -> str:
    variants = pme.build_canary_variants(canary)
    val = variants.for_form(form)
    return f"{val[0]} :: {val[1]}" if isinstance(val, tuple) else val


@pytest.mark.parametrize("form", pme.CANARY_FORMS)
def test_sensitive_threads_show_metadata_only_and_never_leak_canary(form: str) -> None:
    secret_text = f"Example Corp confidential deal memo {_encode_canary(CANARY, form)}"
    emission = pms.emit_panel_entry(
        thread_id=f"th-{secret_text}",
        seat="argus",
        status="running",
        outcome="pending",
        body_md=secret_text,
        ts=_TS,
        level=pm.Level.SENSITIVE,
        config=_CFG,
    )
    view = pmui.project_thread_for_operator_ui(emission, config=_CFG)
    assert view.metadata_only is True
    assert view.body_md is None
    assert view.badge.level is pm.Level.SENSITIVE
    assert view.badge.label == "SENSITIVE"
    assert view.badge.withheld is True
    assert view.badge.visible is True
    assert view.thread_id.startswith("opaque:")
    pme.assert_no_canary_leak(view.to_dict(), CANARY, sink_name="operator_ui:thread")


def test_non_sensitive_and_flag_off_threads_preserve_redacted_content() -> None:
    internal_emission = pms.emit_panel_entry(
        thread_id="th-example-corp-1",
        seat="argus",
        status="running",
        outcome="ok",
        body_md="Example Corp routine status note",
        ts=_TS,
        level=pm.Level.INTERNAL,
        config=_CFG,
    )
    internal_view = pmui.project_thread_for_operator_ui(internal_emission, config=_CFG)
    assert internal_view.metadata_only is False
    assert internal_view.body_md == "Example Corp routine status note"
    assert internal_view.badge.level is pm.Level.INTERNAL
    assert internal_view.badge.visible is True

    # Flag-off behaviour: even if SENSITIVE is passed to emit_panel_entry with DISABLED,
    # output is PUBLIC, badge is hidden, and content is preserved (with secret redaction).
    fake_tok = "ghp_" + "A" * 36
    off_emission = pms.emit_panel_entry(
        thread_id="th-example-corp-off",
        seat="argus",
        status="running",
        outcome="ok",
        body_md=f"Example Corp note with token {fake_tok}",
        ts=_TS,
        level=pm.Level.SENSITIVE,
        config=pm.DISABLED,
    )
    off_view = pmui.project_thread_for_operator_ui(off_emission, config=pm.DISABLED)
    assert off_view.metadata_only is False
    assert off_view.badge.level is pm.Level.PUBLIC
    assert off_view.badge.visible is False
    assert off_view.body_md is not None
    assert "Example Corp note" in off_view.body_md
    assert fake_tok not in off_view.body_md


def test_parked_reasons_and_deadlines_view_projects_admission_and_parking_records() -> None:
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    dec = pma.AdmissionDecision(
        job_id="job-example-corp-sensitive-1",
        state=pma.AdmissionState.PARKED_POLICY,
        effective_level=pm.Level.SENSITIVE,
        actual_destination="local:claude",
        reason="cloud-approval-required",
        owner="pm-rd",
        deadline="2026-10-28T13:00:00Z",
    )
    dec_view = pmui.project_parked_item_for_operator_ui(dec, config=_CFG)
    assert dec_view.job_id.startswith("opaque:")
    assert dec_view.state == "PARKED_POLICY"
    assert dec_view.reason == "cloud-approval-required"
    assert dec_view.owner == "pm-rd"
    assert dec_view.deadline == "2026-10-28T13:00:00Z"
    assert dec_view.badge.level is pm.Level.SENSITIVE
    assert dec_view.badge.withheld is True

    bounds = pmp.parse_parking_bounds(
        {
            "max_depth_per_level": {"PUBLIC": 2, "INTERNAL": 2, "SENSITIVE": 1},
            "max_age_seconds": 600,
            "escalation_owner": "security-oncall",
        }
    )
    queue = pmp.BoundedParkingQueue(bounds=bounds, config=_CFG)
    queue.enqueue(dec, repo=_REPO, now=now)
    dec2 = pma.AdmissionDecision(
        job_id="job-example-corp-sensitive-2",
        state=pma.AdmissionState.PARKED_CAPACITY,
        effective_level=pm.Level.SENSITIVE,
        actual_destination="local:claude",
        reason="lane-capacity-exhausted",
        owner="pm-rd",
        deadline="2026-10-28T13:00:00Z",
    )
    queue.enqueue(dec2, repo=_REPO, now=now)

    views = pmui.list_parked_operator_views(queue, config=_CFG)
    assert len(views) == 2
    by_state = {v.state: v for v in views}
    assert by_state["PARKED_POLICY"].reason == "cloud-approval-required"
    assert by_state["PARKED_POLICY"].deadline == "2026-10-28T12:10:00Z"
    assert by_state["EXPIRED"].reason == "parking-depth-exceeded"
    assert by_state["EXPIRED"].escalated_to == "security-oncall"
    assert by_state["EXPIRED"].expired_at == "2026-10-28T12:00:00Z"


def test_audit_viewer_requires_staff_audit_read_scope(tmp_path: Path) -> None:
    store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3")
    try:
        ev = pca.PrivateModeAuditEvent(
            action="admission",
            actor="dispatcher",
            target_id="job-example-corp-99",
            level=pm.Level.SENSITIVE,
            outcome="PARKED_POLICY",
            metadata={"reason": "agent-not-allowlisted", "destination": "local:claude"},
            ts=_TS,
        )
        store.record_decision(ev, config=_CFG)

        with pytest.raises(PermissionError, match="staff.audit.read"):
            pmui.render_audit_viewer(store, caller_scopes=(), config=_CFG)
        with pytest.raises(PermissionError, match="staff.audit.read"):
            pmui.render_audit_viewer(store, caller_scopes=["staff.read", "operator"], config=_CFG)

        audit_view = pmui.render_audit_viewer(
            store,
            caller_scopes=["staff.read", pmui.AUDIT_READ_SCOPE],
            config=_CFG,
        )
        assert audit_view.chain_ok is True
        assert audit_view.checked_count == 1
        assert len(audit_view.rows) == 1
        row = audit_view.rows[0]
        assert row.action == "admission"
        assert row.outcome == "PARKED_POLICY"
        assert row.level is pm.Level.SENSITIVE
        assert row.target_id.startswith("opaque:")
        assert row.badge.level is pm.Level.SENSITIVE
        assert row.metadata["reason"] == "agent-not-allowlisted"
    finally:
        store.close()


def test_dbc_preconditions_and_publish_guard() -> None:
    assert pme.find_shared_sink_import_violations(_BACKEND) == []
    with pytest.raises(pm.ClassificationError):
        pmui.build_classification_badge("not-a-level")  # type: ignore[arg-type]
    with pytest.raises(pm.ClassificationError):
        pmui.project_thread_for_operator_ui("not-a-projection")  # type: ignore[arg-type]
    unparked = pma.AdmissionDecision("j-1", pma.AdmissionState.READY, pm.Level.PUBLIC, "local:claude")
    with pytest.raises(pm.ClassificationError, match="parked"):
        pmui.project_parked_item_for_operator_ui(unparked, config=_CFG)

    files = ["backend/private_mode_operator_ui.py", "tests/test_private_mode_operator_ui.py"]
    assert pg.check(_ROOT, files, pg.load_rules()) == []
