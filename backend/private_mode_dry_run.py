"""Private Mode PM-21 supervised dry run on the Example Corp placeholder fixture repo (#3272)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ci.private_required_check import PrivateRequiredCheckWiring
else:
    try:
        from backend.ci.private_required_check import PrivateRequiredCheckWiring
    except ModuleNotFoundError:  # pragma: no cover
        from ci.private_required_check import PrivateRequiredCheckWiring

import dashboard_config
from encrypted_transcripts import EncryptedTranscriptStore, TranscriptShreddedError
from key_wrapping import KeyWrappingProvider
from private_context_audit import PrivateContextAuditStore
from private_mode import (
    DISABLED,
    POLICY_FILENAME,
    ClassificationError,
    ClassificationInput,
    Level,
    PrivateModeConfig,
    load_classification_policy,
    resolve,
    stamp_envelope,
)
from private_mode_admission import AdmissionRequest, AdmissionState, PrivateModeAdmissionController
from private_mode_chaos import (
    EXAMPLE_PUBLIC_REPO,
    EXAMPLE_SENSITIVE_REPO,
    _coerce_utc,
    default_chaos_admission_policy,
    default_chaos_overlay_policy,
    evaluate_public_throughput,
    run_chaos_suite,
)
from private_mode_credentials import PrivateModeCredentialBroker, assert_no_credential_files
from private_mode_emit import assert_no_canary_leak, emit_shared
from private_mode_operator_ui import (
    AUDIT_READ_SCOPE,
    OperatorAuditView,
    OperatorThreadView,
    project_thread_for_operator_ui,
    render_audit_viewer,
)
from private_mode_parking import _format_iso
from private_mode_sinks import emit_board_entry, emit_turnover_entry
from staff import fleet_run_token_service
from staff.github_run_env import StaffGithubToken
from staff.roles import RoleSpec

DRY_RUN_CHECKLIST_STEPS: tuple[str, ...] = tuple(
    "overlay_policy classification_and_admission scoped_credentials encrypted_transcript_and_shred "
    "shared_sinks_canary_gate private_ci_and_required_check audit_and_operator_ui "
    "chaos_and_public_throughput rollback_flag_off".split()
)
ENABLE_CHECKLIST_ITEMS: tuple[str, ...] = tuple(
    "dry_run_passed canary_leaks_zero audit_chain_verified chaos_stall_verified rollback_verified "
    "policy_in_overlay_only dieter_signoff".split()
)
_DRY_RUN_RUNBOOK: dict[str, str] = {
    "overlay_policy": "Load overlay classification.json for Example Corp fixture repos (SENSITIVE vs PUBLIC).",
    "classification_and_admission": "Resolve classification and admit an Example Corp SENSITIVE run on its lane.",
    "scoped_credentials": "Mint single-repo short-lived Example Corp credential, scrub HOME, and revoke.",
    "encrypted_transcript_and_shred": "Write Example Corp encrypted transcript, read as pm-rd, and crypto-shred.",
    "shared_sinks_canary_gate": "Emit Example Corp SENSITIVE payloads through sinks and assert zero canary leaks.",
    "private_ci_and_required_check": "Verify PrivateRequiredCheckWiring on Example Corp private-pool workflow.",
    "audit_and_operator_ui": "Project Example Corp SENSITIVE thread and audit trail; verify hash chain.",
    "chaos_and_public_throughput": "Run PM-20 chaos suite on Example Corp and verify PUBLIC throughput.",
    "rollback_flag_off": "Flip private_mode to DISABLED for Example Corp and verify all gates revert to PUBLIC.",
}
EXPORTS: frozenset[str] = frozenset(
    "DRY_RUN_CHECKLIST_STEPS DryRunReport DryRunStepResult ENABLE_CHECKLIST_ITEMS EnableChecklistReport "
    "RollbackVerificationResult evaluate_enable_checklist get_dry_run_runbook run_supervised_dry_run "
    "verify_flag_off_rollback".split()
)


@dataclass(frozen=True)
class DryRunStepResult:
    step: str
    passed: bool
    detail: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class RollbackVerificationResult:
    verified: bool
    flag_off_level: Level
    key_provider_enabled: bool
    transcript_store_enabled: bool
    required_check_state: str
    shared_surface_withheld: bool
    parked_sensitive_count: int


@dataclass(frozen=True)
class EnableChecklistReport:
    items: Mapping[str, bool]
    all_technical_gates_passed: bool
    dieter_signoff: bool
    ready_to_enable: bool
    runbook_steps: tuple[str, ...]


@dataclass(frozen=True)
class DryRunReport:
    repo: str
    config: PrivateModeConfig
    steps: Mapping[str, DryRunStepResult]
    passed: bool
    rollback: RollbackVerificationResult
    enable_checklist: EnableChecklistReport
    operator_thread_view: OperatorThreadView
    operator_audit_view: OperatorAuditView


def get_dry_run_runbook() -> Mapping[str, str]:
    """Return the ordered supervised dry-run runbook for the Example Corp fixture repo."""
    return dict(_DRY_RUN_RUNBOOK)


def _write_overlay_fixture(overlay_dir: Path) -> Path:
    overlay_dir.mkdir(parents=True, exist_ok=True)
    policy_file = overlay_dir / POLICY_FILENAME
    repos = [{"repo": EXAMPLE_SENSITIVE_REPO, "level": "SENSITIVE"}, {"repo": EXAMPLE_PUBLIC_REPO, "level": "PUBLIC"}]
    payload = {"default_level": "INTERNAL", "repos": repos, "path_globs": [], "operator_marks": {}}
    policy_file.write_text(json.dumps(payload), encoding="utf-8")
    return policy_file


def verify_flag_off_rollback(
    *, work_dir: Path | str, canary: str = "ExampleCorpDryRunCanary42", now: datetime | None = None
) -> RollbackVerificationResult:
    """Verify that setting ``private_mode`` to ``DISABLED`` cleanly rolls back all Private Mode gates."""
    if not isinstance(canary, str) or len(canary.strip()) < 8:
        raise ClassificationError("verify_flag_off_rollback: canary must be a non-empty string of length >= 8")
    root, now_dt, policy = Path(work_dir), _coerce_utc(now), default_chaos_overlay_policy()
    root.mkdir(parents=True, exist_ok=True)
    resolved_off = resolve(ClassificationInput(repo=EXAMPLE_PUBLIC_REPO), policy=policy, config=DISABLED)
    sens_off = resolve(ClassificationInput(repo=EXAMPLE_SENSITIVE_REPO), policy=policy, config=DISABLED)
    key_prov = KeyWrappingProvider(backend="vault", private_mode=DISABLED, env={})
    tx_store = EncryptedTranscriptStore(store_dir=root / "tx_off", key_provider=key_prov, private_mode=DISABLED)
    wiring = PrivateRequiredCheckWiring(overlay_policy=policy, private_mode=DISABLED)
    wf = [{"name": "ci", "jobs": {"test": {"runs-on": "ubuntu-latest"}}}]
    c_kw: dict[str, Any] = dict(run_id="rollback01", repo=EXAMPLE_SENSITIVE_REPO, sha="a" * 40, target_kind="pr_head")
    chk = wiring.evaluate_check(pool_healthy=False, workflows=wf, enabled=False, **c_kw)
    board_in = {"id": "b-off", "repo": EXAMPLE_PUBLIC_REPO, "author": "pm-rd", "status": "ok", "body": "public ok"}
    board_proj = emit_shared("board", board_in, level=Level.PUBLIC, config=DISABLED)
    sens_proj = emit_shared("board", board_in, level=Level.SENSITIVE, config=DISABLED)
    suite_off = run_chaos_suite(
        work_dir=root / "chaos_off", canary=canary.strip(), config=DISABLED, private_ci_enabled=False, now=now_dt
    )
    parked_sens = len(suite_off.parking_queue.list_parked(Level.SENSITIVE))
    kp_on, tx_on = key_prov.is_enabled(), tx_store.is_enabled()
    ok = (
        resolved_off is Level.PUBLIC
        and sens_off is Level.SENSITIVE
        and sens_proj.withheld
        and not (kp_on or tx_on or board_proj.withheld)
        and chk.state == "disabled"
        and parked_sens == 0
    )
    return RollbackVerificationResult(ok, resolved_off, kp_on, tx_on, chk.state, board_proj.withheld, parked_sens)


def evaluate_enable_checklist(
    *,
    steps: Mapping[str, DryRunStepResult],
    rollback: RollbackVerificationResult,
    audit_chain_ok: bool,
    policy_in_overlay_only: bool = True,
    dieter_signoff: bool = False,
) -> EnableChecklistReport:
    """Evaluate the PM-21 enable checklist; ``ready_to_enable`` requires Dieter's explicit sign-off."""
    if not isinstance(steps, Mapping) or tuple(steps) != DRY_RUN_CHECKLIST_STEPS:
        raise ClassificationError("evaluate_enable_checklist: steps must match DRY_RUN_CHECKLIST_STEPS in order")
    if not isinstance(rollback, RollbackVerificationResult):
        raise ClassificationError("evaluate_enable_checklist: rollback must be a RollbackVerificationResult")
    if not all(isinstance(b, bool) for b in (audit_chain_ok, policy_in_overlay_only, dieter_signoff)):
        raise ClassificationError("evaluate_enable_checklist: boolean flags must be of type bool")

    tech_items = {
        "dry_run_passed": all(s.passed for s in steps.values()),
        "canary_leaks_zero": steps["shared_sinks_canary_gate"].passed,
        "audit_chain_verified": bool(audit_chain_ok and steps["audit_and_operator_ui"].passed),
        "chaos_stall_verified": steps["chaos_and_public_throughput"].passed,
        "rollback_verified": bool(rollback.verified and steps["rollback_flag_off"].passed),
        "policy_in_overlay_only": policy_in_overlay_only,
    }
    all_tech = all(tech_items.values())
    items = {**tech_items, "dieter_signoff": dieter_signoff}
    return EnableChecklistReport(
        items, all_tech, dieter_signoff, bool(all_tech and dieter_signoff), DRY_RUN_CHECKLIST_STEPS
    )


def _mint_scoped_credential_step(root: Path, decision: Any, cfg: PrivateModeConfig, epoch_s: float) -> DryRunStepResult:
    perms = {"push_branch": False, "open_pr": False, "merge": False}
    role = RoleSpec(
        name="pm-worker",
        title="Example Corp",
        repos=(EXAMPLE_SENSITIVE_REPO,),
        scope={"code_read_only": True},
        permissions=perms,
    )
    tok = StaffGithubToken(token="ghs_example_corp_ephemeral", expires_at=epoch_s + 900.0, source="github_app")
    broker = PrivateModeCredentialBroker(config=cfg, token_minter=lambda **_: tok)
    prev_dc_org, prev_fts_org = dashboard_config.ORG, fleet_run_token_service.ORG
    try:
        dashboard_config.ORG = fleet_run_token_service.ORG = "example-corp"
        cred = broker.mint_for_admitted(
            decision=decision, role=role, repositories=[EXAMPLE_SENSITIVE_REPO], caller_node="node-1", now=epoch_s
        )
    finally:
        dashboard_config.ORG, fleet_run_token_service.ORG = prev_dc_org, prev_fts_org

    sb_dir = root / "sandboxes"
    sb_dir.mkdir(parents=True, exist_ok=True)
    with broker.ephemeral_sandbox_env(
        cred.credential_id, base_env={"PATH": "/usr/bin:/bin"}, parent_dir=sb_dir, now=epoch_s + 1.0
    ) as sb_env:
        assert_no_credential_files(Path(sb_env["HOME"]))
    revoked_ok = False
    try:
        broker.resolve_token(cred.credential_id, now=epoch_s + 2.0)
    except ClassificationError:
        revoked_ok = True
    ok = bool(cred.repo == EXAMPLE_SENSITIVE_REPO and revoked_ok and list(sb_dir.iterdir()) == [])
    meta = {"repo": cred.repo, "level": cred.level.name, "revoked_on_exit": revoked_ok}
    return DryRunStepResult("scoped_credentials", ok, "scoped token minted and revoked", meta)


def run_supervised_dry_run(
    *,
    work_dir: Path | str,
    canary: str = "ExampleCorpDryRunCanary42",
    config: PrivateModeConfig | None = None,
    dieter_signoff: bool = False,
    now: datetime | None = None,
) -> DryRunReport:
    """Execute the PM-21 supervised dry run end-to-end on the Example Corp placeholder fixture repo."""
    if not isinstance(canary, str) or len(canary.strip()) < 8:
        raise ClassificationError("run_supervised_dry_run: canary must be a non-empty string of length >= 8")
    cfg = PrivateModeConfig(enabled=True, default_level=Level.INTERNAL) if config is None else config
    if not isinstance(cfg, PrivateModeConfig):
        raise ClassificationError("run_supervised_dry_run: config must be a PrivateModeConfig")
    if not isinstance(dieter_signoff, bool):
        raise ClassificationError("run_supervised_dry_run: dieter_signoff must be a bool")

    root, now_dt, canary_val = Path(work_dir), _coerce_utc(now), canary.strip()
    root.mkdir(parents=True, exist_ok=True)
    now_iso, epoch_s = _format_iso(now_dt), now_dt.timestamp()
    policy = load_classification_policy(_write_overlay_fixture(root / "overlay"))
    sens_level = resolve(ClassificationInput(repo=EXAMPLE_SENSITIVE_REPO), policy=policy, config=cfg)
    pub_level = resolve(ClassificationInput(repo=EXAMPLE_PUBLIC_REPO), policy=policy, config=cfg)
    env = stamp_envelope(sens_level, hop="dry_run_origin", config=cfg)
    exp_sens = Level.SENSITIVE if cfg.enabled else Level.PUBLIC
    s1 = DryRunStepResult(
        "overlay_policy",
        bool(sens_level is Level.SENSITIVE and pub_level is Level.PUBLIC and env.level is Level.SENSITIVE),
        "overlay classification.json validated",
        {"sensitive_level": sens_level.name, "public_level": pub_level.name},
    )

    audit_store = PrivateContextAuditStore(root / "dry_run_audit.sqlite3")
    gate = PrivateModeAdmissionController(policy=default_chaos_admission_policy(), config=cfg, audit_store=audit_store)
    adm_dec = gate.admit(AdmissionRequest("job-dryrun-1", EXAMPLE_SENSITIVE_REPO, env, "claude", "claude"), now=now_dt)
    s2 = DryRunStepResult(
        "classification_and_admission",
        bool(adm_dec.state is AdmissionState.READY and adm_dec.effective_level is exp_sens),
        "Example Corp job admitted",
        {"state": adm_dec.state.value, "effective_level": adm_dec.effective_level.name},
    )
    s3 = _mint_scoped_credential_step(root, adm_dec, cfg, epoch_s)

    key_dir = root / "keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path = key_dir / "vault.key"
    key_path.write_bytes(b"k" * 32)
    os.chmod(key_path, 0o600)
    kp = KeyWrappingProvider(backend="vault", private_mode=cfg, env={"RUNNER_DASHBOARD_VAULT_KEY_PATH": str(key_path)})
    tx_store = EncryptedTranscriptStore(store_dir=root / "transcripts", key_provider=kp, private_mode=cfg)
    if cfg.enabled:
        tx_store.create_thread("dry-run-thread-1", authorized_principals=("pm-rd",), ttl_seconds=3600, now=epoch_s)
        tx_store.append_chunk("dry-run-thread-1", canary_val.encode("utf-8"), now=epoch_s + 1.0)
        chunks = tx_store.read_chunks("dry-run-thread-1", principal="pm-rd", now=epoch_s + 2.0)
        tx_store.crypto_shred("dry-run-thread-1", reason="dry-run-complete", now=epoch_s + 3.0)
        shred_ok = False
        try:
            tx_store.read_chunks("dry-run-thread-1", principal="pm-rd", now=epoch_s + 4.0)
        except TranscriptShreddedError:
            shred_ok = True
        tx_passed = chunks == (canary_val.encode("utf-8"),) and shred_ok
    else:
        tx_passed = not tx_store.is_enabled()
    s4 = DryRunStepResult(
        "encrypted_transcript_and_shred", tx_passed, "transcript shredded", {"enabled": tx_store.is_enabled()}
    )

    body_txt = f"working on {canary_val}" if cfg.enabled else "public dry run"
    sink_opts: dict[str, Any] = {"level": exp_sens, "config": cfg, "ts": now_iso}
    b_em = emit_board_entry(
        entry_id="b-1", repo=EXAMPLE_SENSITIVE_REPO, author="pm-rd", status="ok", body=body_txt, **sink_opts
    )
    t_em = emit_turnover_entry(
        entry_id="t-1", scope=EXAMPLE_SENSITIVE_REPO, author="pm-rd", status="ok", summary=body_txt, **sink_opts
    )
    panel_in = dict(id="p-1", repo=EXAMPLE_SENSITIVE_REPO, status="running", outcome="ok", body_md=body_txt, ts=now_iso)
    p_proj = emit_shared("panels", panel_in, level=exp_sens, config=cfg)
    if cfg.enabled:
        for name, payload in (("board", b_em.formatted), ("turnover", t_em.formatted), ("panels", p_proj.to_dict())):
            assert_no_canary_leak(payload, canary_val, sink_name=name)
    s5 = DryRunStepResult(
        "shared_sinks_canary_gate",
        bool(b_em.projection.withheld == cfg.enabled and p_proj.withheld == cfg.enabled),
        "shared sinks verified",
        {"withheld": p_proj.withheld, "surface_count": 3},
    )

    wiring = PrivateRequiredCheckWiring(overlay_policy=policy, private_mode=cfg, audit_store=audit_store)
    wf = [{"name": "ci", "jobs": {"test": {"runs-on": "private-pool"}}}]
    chk_kw: dict[str, Any] = dict(
        run_id="dryrun001", repo=EXAMPLE_SENSITIVE_REPO, sha="a" * 40, target_kind="pr_head", now=now_iso
    )
    chk_res = wiring.evaluate_check(
        pool_healthy=True, workflows=wf, enabled=cfg.enabled, attested_state="success", **chk_kw
    )
    s6 = DryRunStepResult(
        "private_ci_and_required_check",
        bool(chk_res.state == ("success" if cfg.enabled else "disabled") and not chk_res.auto_passed),
        "private required check evaluated",
        {"check_state": chk_res.state, "auto_passed": chk_res.auto_passed},
    )

    thread_view = project_thread_for_operator_ui(p_proj, config=cfg)
    audit_view = render_audit_viewer(audit_store, caller_scopes=(AUDIT_READ_SCOPE,), config=cfg)
    s7 = DryRunStepResult(
        "audit_and_operator_ui",
        bool(audit_view.chain_ok and thread_view.metadata_only == cfg.enabled),
        "operator UI projection and audit hash chain verified",
        {"chain_ok": audit_view.chain_ok, "metadata_only": thread_view.metadata_only},
    )

    tp = evaluate_public_throughput(work_dir=root / "chaos", public_job_count=8, tolerance=0.15, config=cfg, now=now_dt)
    s8 = DryRunStepResult(
        "chaos_and_public_throughput",
        tp.within_tolerance,
        "chaos faults parked while PUBLIC throughput stayed within tolerance",
        {"within_tolerance": tp.within_tolerance, "public_completed": tp.public_completed},
    )

    rollback_res = verify_flag_off_rollback(work_dir=root / "rollback", canary=canary_val, now=now_dt)
    s9 = DryRunStepResult(
        "rollback_flag_off", rollback_res.verified, "flag-off rollback verified", {"verified": rollback_res.verified}
    )

    steps = {s.step: s for s in (s1, s2, s3, s4, s5, s6, s7, s8, s9)}
    signoff_ok = bool(dieter_signoff and cfg.enabled)
    checklist = evaluate_enable_checklist(
        steps=steps, rollback=rollback_res, audit_chain_ok=audit_view.chain_ok, dieter_signoff=signoff_ok
    )
    all_passed = all(s.passed for s in steps.values())
    return DryRunReport(
        EXAMPLE_SENSITIVE_REPO, cfg, steps, all_passed, rollback_res, checklist, thread_view, audit_view
    )
