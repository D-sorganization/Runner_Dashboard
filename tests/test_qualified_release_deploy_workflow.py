"""Contracts for the guarded OGLaptop release deployment (issue #1138)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-qualified-release.yml"
BOOTSTRAP = ROOT / "deploy" / "bootstrap-qualified-release-deploy.sh"
TRANSACTION = ROOT / "deploy" / "qualified-release-deploy.sh"
LIBRARY = ROOT / "deploy" / "qualified-release-lib.sh"
RUNTIME_LIBRARY = ROOT / "deploy" / "qualified-release-runtime-lib.sh"
SCHEDULE = ROOT / "config" / "runner-schedule-oglaptop.json"
RUNBOOK = ROOT / "docs" / "runbooks" / "qualified-release-deploy.md"
PINNED_ACTION = re.compile(r"^[\w.\-]+/[\w.\-]+(?:/[\w.\-/]+)?@[0-9a-f]{40}$")


def _workflow() -> dict:  # type: ignore[type-arg]
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    if True in payload and "on" not in payload:
        payload["on"] = payload[True]
    return payload


def _uses() -> list[str]:
    refs: list[str] = []
    for raw in WORKFLOW.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("uses:"):
            refs.append(line.split(":", 1)[1].split("#", 1)[0].strip().strip("'\""))
    return refs


def test_guarded_deployment_assets_exist() -> None:
    for path in (WORKFLOW, BOOTSTRAP, TRANSACTION, LIBRARY, RUNTIME_LIBRARY, SCHEDULE, RUNBOOK):
        assert path.is_file(), path


def test_workflow_is_manual_exact_host_and_serialized() -> None:
    payload = _workflow()
    assert set(payload["on"]) == {"workflow_dispatch"}
    job = payload["jobs"]["deploy"]
    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "d-sorg-local-Oglaptop-1"]
    assert job["environment"] == {"name": "oglaptop-production"}
    assert job["timeout-minutes"] >= 12
    assert payload["concurrency"] == {
        "group": "qualified-release-deploy-oglaptop",
        "cancel-in-progress": False,
    }


def test_workflow_accepts_only_exact_release_identity() -> None:
    inputs = _workflow()["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "target",
        "version",
        "tag",
        "release_commit",
        "artifact_sha256",
        "confirm",
    }
    assert inputs["target"]["type"] == "choice"
    assert inputs["target"]["options"] == ["OGLaptop"]
    assert all(item["required"] is True for item in inputs.values())
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "DEPLOY OGLAPTOP" in text
    assert "inputs.command" not in text
    assert "inputs.script" not in text


def test_workflow_permissions_actions_and_supply_chain_are_closed() -> None:
    payload = _workflow()
    assert payload["permissions"] == {"contents": "read", "attestations": "read", "id-token": "none"}
    refs = _uses()
    assert refs
    assert all(PINNED_ACTION.match(ref) for ref in refs)
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "GH_TOKEN: ${{ secrets.OGLAPTOP_DEPLOY_GITHUB_TOKEN }}" in text
    assert "${{ github.token }}" not in text
    assert "orgs/D-sorganization/actions/runners" in text
    for marker in (
        "refs/tags/",
        "git/refs/tags/",
        "git/tags/",
        "branches/main",
        "gh attestation verify",
        "cosign verify-blob",
        "sha256sum -c",
        "runner-dashboard-artifact-v2",
        "python_minor",
    ):
        assert marker in text


def test_workflow_proves_only_its_exact_worker_is_busy() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "RUNNER_NAME",
        "d-sorg-local-Oglaptop-1",
        "Runner.Worker",
        "WORKER_PID",
        "--paginate",
        "total_count",
        '"busy":true',
        "every other OGLaptop runner must be idle",
    ):
        assert marker in text


def test_workflow_calls_only_preinstalled_exact_sudo_command() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "sudo -n /usr/local/sbin/runner-dashboard-qualified-deploy" in text
    assert "deploy/setup.sh" not in text
    assert "install-runner-maintenance.sh" not in text
    assert "systemctl start actions.runner" not in text
    assert "systemctl stop actions.runner" not in text


def test_bootstrap_installs_root_authority_and_narrow_sudoers() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    for marker in (
        "/usr/local/sbin/runner-dashboard-qualified-deploy",
        "/usr/local/lib/runner-dashboard/qualified-release-lib.sh",
        "/etc/sudoers.d/runner-dashboard-qualified-deploy",
        "visudo -cf",
        "root:root",
        "0440",
        "0755",
        "--expected-commit",
        "status --porcelain",
    ):
        assert marker in text
    assert "NOPASSWD: ${HELPER_DEST}" in text
    assert "runner-dashboard-qualified-deploy *" not in text
    assert "ALL=(ALL)" not in text


def test_transaction_has_strict_request_and_host_contracts() -> None:
    text = TRANSACTION.read_text(encoding="utf-8") + LIBRARY.read_text(encoding="utf-8")
    for marker in (
        "qualified-release-request-v1",
        "d-sorg-local-Oglaptop-1",
        "runner-dashboard-artifact-v2",
        "runner-dashboard.service",
        "Python 3.12",
        "validate_worker_ancestry",
        "validate_local_runner_inventory",
        "runner-1",
        "weekday-day",
        "weekend-day",
    ):
        assert marker in text
    assert "actions.runner.*" not in text
    assert "migrate-runner-units" not in text


def test_transaction_verifies_signed_archive_before_mutation() -> None:
    text = TRANSACTION.read_text(encoding="utf-8")
    mutation = text.index("begin_mutation")
    for marker in (
        "verify_sha256",
        "verify_cosign_bundle",
        "validate_archive",
        "validate_artifact_metadata",
        "validate_local_runner_inventory",
    ):
        assert text.index(marker) < mutation
    library = LIBRARY.read_text(encoding="utf-8")
    assert "--certificate-identity-regexp" in library
    assert "--certificate-oidc-issuer" in library


def test_transaction_journals_and_rolls_back_all_host_mutations() -> None:
    text = TRANSACTION.read_text(encoding="utf-8") + RUNTIME_LIBRARY.read_text(encoding="utf-8")
    library = LIBRARY.read_text(encoding="utf-8")
    for marker in (
        "trap transaction_exit EXIT",
        "record_rollback_baseline",
        "create_quiesced_rollback_snapshot",
        "create_mutable_manifest",
        "restore_rollback_snapshot",
        "journal_event",
        "ROLLBACK_SNAPSHOT",
        "mutable-before.json",
        "mutable-after.json",
        "systemd-before",
    ):
        assert marker in text or marker in library
    assert "rsync" in library
    assert "sha256" in library


def test_transaction_quiesces_writers_before_consistent_snapshot() -> None:
    text = TRANSACTION.read_text(encoding="utf-8")
    baseline = text.index("record_rollback_baseline\nMUTATION_STARTED=1")
    mutation = text.index('journal_event "begin_mutation"', baseline)
    quiesce = text.index("quiesce_for_snapshot", mutation)
    snapshot = text.index("create_quiesced_rollback_snapshot", quiesce)
    manifest = text.index("create_mutable_manifest", snapshot)
    assert baseline < mutation < quiesce < snapshot < manifest

    library = LIBRARY.read_text(encoding="utf-8")
    for marker in (
        "systemctl stop runner-scheduler.timer",
        "systemctl stop runner-scheduler.service",
        "systemctl stop runner-dashboard.service",
        "systemctl disable --now runner-autoscaler.service",
        "verify_quiesced_snapshot",
    ):
        assert marker in library


def test_partial_snapshot_failure_restores_only_baseline_authority() -> None:
    transaction = TRANSACTION.read_text(encoding="utf-8")
    manifest = transaction.index('create_mutable_manifest "${MUTABLE_BEFORE}"')
    complete = transaction.index("mark_quiesced_snapshot_complete", manifest)
    installer = transaction.index("install-dashboard-artifact.sh", complete)
    assert manifest < complete < installer

    library = LIBRARY.read_text(encoding="utf-8")
    gate = library.index('if [[ -f "${SNAPSHOT_COMPLETE}"')
    dashboard_restore = library.index('restore_path "${ROLLBACK_SNAPSHOT}/dashboard"', gate)
    unit_restore = library.index('restore_path "${ROLLBACK_SNAPSHOT}/systemd-before/30-qualified-capacity.conf"')
    assert gate < dashboard_restore < unit_restore
    assert 'install -o root -g root -m 0400 /dev/null "${SNAPSHOT_COMPLETE}"' in library


def test_canonical_oglaptop_schedule_is_bounded() -> None:
    schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    assert schedule["enabled"] is True
    assert schedule["timezone"] == "America/Los_Angeles"
    assert schedule["default_count"] == 4
    assert schedule["max_count"] == 8
    counts = {item["name"]: item["runners"] for item in schedule["schedules"]}
    assert counts == {"weekday-day": 4, "weekend-day": 4, "overnight": 4}


def test_transaction_enforces_governed_runtime_and_scheduler_authority() -> None:
    text = (
        TRANSACTION.read_text(encoding="utf-8")
        + LIBRARY.read_text(encoding="utf-8")
        + RUNTIME_LIBRARY.read_text(encoding="utf-8")
    )
    library = LIBRARY.read_text(encoding="utf-8")
    for marker in (
        "NUM_RUNNERS=4",
        "MAX_RUNNERS=8",
        "30-qualified-capacity.conf",
        "QUALIFIED_SCHEDULER_RUNTIME",
        "QUALIFIED_SCHEDULER_BIN",
        "RUNNER_SCHEDULE_CONFIG",
        "runner-scheduler.timer",
        "OnUnitActiveSec=5m",
        "runner-autoscaler.service",
        "disable --now",
    ):
        assert marker in text
    assert "/opt/runner-dashboard-qualified/releases" in library
    assert "ExecStart=${QUALIFIED_SCHEDULER_RUNTIME}/bin/python ${QUALIFIED_SCHEDULER_BIN} --apply" in text
    assert "ExecStart=${QUALIFIED_DEPLOY_DIR}/.venv/bin/python" not in text
    assert "/usr/local/bin/runner-scheduler --apply" not in text
    assert "verify_root_owned_scheduler_runtime" in text
    assert "-m venv --copies --without-pip" in text
    assert 'find "${QUALIFIED_SCHEDULER_RELEASE}" -type l' in text
    assert 'chmod 0555 "${candidate}"/runtime/bin/python*' in text


def test_transaction_verifies_health_identity_state_and_full_cycle() -> None:
    text = TRANSACTION.read_text(encoding="utf-8")
    for marker in (
        "/health",
        "/livez",
        "verify_deployment_identity",
        "verify_mutable_manifest",
        "InvocationID",
        "SCHEDULER_CYCLE_TIMEOUT_SECONDS=330",
        "verify_scheduler_steady_state",
        "actions",
    ):
        assert marker in text


def test_evidence_is_redacted_and_uploaded_even_on_failure() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    transaction = TRANSACTION.read_text(encoding="utf-8")
    assert "if: always()" in workflow
    assert "actions/upload-artifact" in workflow
    assert "redacted-evidence" in workflow
    assert "write_redacted_evidence" in transaction
    for forbidden in ("GH_TOKEN", "GITHUB_TOKEN", "EnvironmentFile", "journalctl"):
        assert forbidden not in transaction
