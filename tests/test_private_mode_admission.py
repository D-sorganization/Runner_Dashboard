"""Contract tests for Private Mode PM-10 admission policy and explicit states (issue #3261, epic #3249)."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("fastapi")
    _stub.HTTPException = Exception  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _stub

import private_mode as pm  # noqa: E402
import private_mode_admission as pma  # noqa: E402
from private_context_audit import PrivateContextAuditStore  # noqa: E402
from staff.exclusions import ExclusionsList  # noqa: E402

_CFG = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
_MODES = ["local", "cloud"]


def _sample_policy() -> pma.AdmissionPolicy:
    pub_d = ["local:antigravity", "local:claude", "local:codex", "cloud:claude", "cloud:codex", "cloud:cursor-agent"]
    int_d = ["local:antigravity", "local:claude", "local:codex", "cloud:claude"]
    lanes = {
        "PUBLIC": (4, ["antigravity", "claude", "codex", "cursor-agent"], pub_d, False),
        "INTERNAL": (2, ["antigravity", "claude", "codex"], int_d, True),
        "SENSITIVE": (1, ["antigravity", "claude"], ["local:antigravity", "local:claude"], True),
    }
    raw_lanes = {
        k: {
            "max_concurrent": c,
            "allowed_agents": ag,
            "allowed_destinations": d,
            "allowed_modes": _MODES,
            "requires_cloud_approval": req,
        }
        for k, (c, ag, d, req) in lanes.items()
    }
    return pma.parse_admission_policy({"lanes": raw_lanes, "default_owner": "pm-rd", "default_deadline_seconds": 3600})


def _req(
    job_id: str,
    level: pm.Level,
    *,
    repo: str = "example-corp/widgets-sensitive",
    agent: str = "claude",
    provider: str = "claude",
    mode: str = "local",
    argv: tuple[str, ...] = (),
    issue: int | None = None,
    cloud_approved: bool = False,
    keys_ready: bool = True,
) -> pma.AdmissionRequest:
    env = pm.stamp_envelope(level, hop="dispatch", config=_CFG)
    return pma.AdmissionRequest(
        job_id, repo, env, agent, provider, mode, argv, issue, (), "dispatcher", None, cloud_approved, keys_ready
    )


def test_admission_state_enum_and_no_hol_block_for_public(tmp_path: Path) -> None:
    """Acceptance criterion 1: a blocked SENSITIVE job causes no head-of-line block for PUBLIC."""
    expected = {"READY", "RUNNING", "PARKED_POLICY", "PARKED_CAPACITY", "PARKED_AUDIT", "FAILED", "CANCELLED"}
    assert {s.value for s in pma.AdmissionState} == expected
    ctrl = pma.PrivateModeAdmissionController(
        policy=_sample_policy(), config=_CFG, audit_store=PrivateContextAuditStore(tmp_path / "a.db")
    )
    now = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)
    dec_1 = ctrl.admit_and_spawn(_req("job-sens-1", pm.Level.SENSITIVE), spawner=lambda r: f"pid-{r.job_id}", now=now)
    assert dec_1.state is pma.AdmissionState.RUNNING and dec_1.spawn_handle == "pid-job-sens-1"

    dec_2 = ctrl.admit(_req("job-sens-2", pm.Level.SENSITIVE), now=now)
    assert dec_2.state is pma.AdmissionState.PARKED_CAPACITY and dec_2.reason == "lane-capacity-exhausted"
    assert dec_2.owner == "pm-rd"
    assert dec_2.deadline == (now + timedelta(seconds=3600)).isoformat().replace("+00:00", "Z")

    dec_3 = ctrl.admit(_req("job-sens-3", pm.Level.SENSITIVE, keys_ready=False), now=now)
    assert dec_3.state is pma.AdmissionState.PARKED_POLICY and dec_3.reason == "keys-unavailable"

    spawned_public: list[str] = []
    pub_req = _req("job-pub-1", pm.Level.PUBLIC, repo="example-corp/widgets-public", keys_ready=False)
    dec_pub = ctrl.admit_and_spawn(pub_req, spawner=lambda r: spawned_public.append(r.job_id), now=now)
    assert dec_pub.state is pma.AdmissionState.RUNNING and spawned_public == ["job-pub-1"]


def test_cloud_without_approval_or_unapproved_actual_destination_parks_policy(tmp_path: Path) -> None:
    """Acceptance criterion 2: cloud without approval -> PARKED_POLICY, judged on actual destination."""
    ctrl = pma.PrivateModeAdmissionController(
        policy=_sample_policy(), config=_CFG, audit_store=PrivateContextAuditStore(tmp_path / "a.db")
    )
    dec_unapp = ctrl.admit(_req("job-int-1", pm.Level.INTERNAL, repo="example-corp/widgets-internal", mode="cloud"))
    assert dec_unapp.state is pma.AdmissionState.PARKED_POLICY and dec_unapp.reason == "cloud-approval-required"

    dec_ok = ctrl.admit(
        _req("job-int-2", pm.Level.INTERNAL, repo="example-corp/widgets-internal", mode="cloud", cloud_approved=True)
    )
    assert dec_ok.state is pma.AdmissionState.READY

    disguised = _req("job-disg", pm.Level.SENSITIVE, mode="local", argv=("claude", "--remote"), cloud_approved=True)
    dec_disguised = ctrl.admit(disguised)
    assert dec_disguised.state is pma.AdmissionState.PARKED_POLICY
    assert dec_disguised.reason == "destination-not-allowlisted" and dec_disguised.actual_destination == "cloud:claude"


def test_no_cli_spawned_before_pre_spawn_checks_pass(tmp_path: Path) -> None:
    """Acceptance criterion 3: no CLI spawned before checks pass (keys, audit, approvals, exclusions, bypass flags)."""
    audit = PrivateContextAuditStore(tmp_path / "audit.sqlite3")
    excl = ExclusionsList(tmp_path / "exclusions.json")
    excl.add({"repo": "example-corp/widgets-sensitive", "issue": 99, "owner": "lane-other", "reason": "held"})
    ctrl = pma.PrivateModeAdmissionController(policy=_sample_policy(), config=_CFG, audit_store=audit, exclusions=excl)
    spawn_calls: list[str] = []

    def _spawner(req: pma.AdmissionRequest) -> str:
        spawn_calls.append(req.job_id)
        return f"spawned-{req.job_id}"

    res_keys = ctrl.admit_and_spawn(_req("job-no-keys", pm.Level.SENSITIVE, keys_ready=False), spawner=_spawner)
    assert res_keys.state is pma.AdmissionState.PARKED_POLICY and res_keys.reason == "keys-unavailable"

    bypass_req = _req("job-bypass", pm.Level.SENSITIVE, argv=("claude", "--dangerously-skip-permissions"))
    res_bypass = ctrl.admit_and_spawn(bypass_req, spawner=_spawner)
    assert res_bypass.state is pma.AdmissionState.PARKED_POLICY and res_bypass.reason == "adapter-policy-bypass-flag"

    res_excl = ctrl.admit_and_spawn(_req("job-excluded", pm.Level.SENSITIVE, issue=99), spawner=_spawner)
    assert res_excl.state is pma.AdmissionState.PARKED_POLICY and res_excl.reason == "dispatch-excluded"
    assert res_excl.owner == "lane-other"

    audit.close()
    res_audit = ctrl.admit_and_spawn(_req("job-audit-down", pm.Level.SENSITIVE), spawner=_spawner)
    assert res_audit.state is pma.AdmissionState.PARKED_AUDIT and res_audit.reason == "audit-unavailable"
    assert spawn_calls == []


def test_flag_off_and_explicit_lifecycle_transitions(tmp_path: Path) -> None:
    """Acceptance criterion 4 + lifecycle: Flag-off unchanged; transitions release capacity and sanitize reasons."""
    ctrl_off = pma.PrivateModeAdmissionController(
        policy=_sample_policy(), config=pm.DISABLED, audit_store=PrivateContextAuditStore(tmp_path / "off.db")
    )
    spawned: list[str] = []
    env = pm.ClassificationEnvelope(pm.ENVELOPE_SCHEMA_VERSION, pm.Level.SENSITIVE, 1, ("dispatch",))
    req = pma.AdmissionRequest("job-off", "example-corp/widgets-sensitive", env, "cursor-agent", "cursor", "cloud")
    dec = ctrl_off.admit_and_spawn(req, spawner=lambda r: spawned.append(r.job_id))
    assert dec.state is pma.AdmissionState.RUNNING and dec.effective_level is pm.Level.PUBLIC and spawned == ["job-off"]

    ctrl = pma.PrivateModeAdmissionController(
        policy=_sample_policy(), config=_CFG, audit_store=PrivateContextAuditStore(tmp_path / "on.db")
    )
    dec1 = ctrl.admit_and_spawn(_req("job-1", pm.Level.SENSITIVE), spawner=lambda r: "pid-1")
    assert dec1.state is pma.AdmissionState.RUNNING
    cancelled = ctrl.transition("job-1", pma.AdmissionState.CANCELLED, reason="operator-cancelled")
    assert cancelled.state is pma.AdmissionState.CANCELLED and cancelled.reason == "operator-cancelled"

    dec2 = ctrl.admit_and_spawn(_req("job-2", pm.Level.SENSITIVE), spawner=lambda r: "pid-2")
    assert dec2.state is pma.AdmissionState.RUNNING
    failed = ctrl.transition("job-2", pma.AdmissionState.FAILED, reason="exit-code-nonzero")
    assert failed.state is pma.AdmissionState.FAILED
    with pytest.raises(pm.ClassificationError, match="sanitized reason"):
        ctrl.transition("job-2", pma.AdmissionState.FAILED, reason="raw secret token leaked in error")


def test_new_admission_files_pass_publish_guard() -> None:
    """Acceptance criterion 5: Passes the #3080 publish guard; placeholder fixtures only ('Example Corp')."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    import publish_guard as pg  # noqa: PLC0415

    paths = ["backend/private_mode_admission.py", "tests/test_private_mode_admission.py"]
    assert pg.check(_ROOT, paths, pg.load_rules()) == []
