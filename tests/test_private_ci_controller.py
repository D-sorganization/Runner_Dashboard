"""Contract tests for Private Mode PM-16 private CI controller + status publisher (issue #3267, epic #3249)."""

from __future__ import annotations

import dataclasses
import datetime as _dt_mod
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
for _p in (_ROOT, _BACKEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from key_wrapping import KeyWrappingProvider  # noqa: E402
from private_mode import ClassificationError, Level, PrivateModeConfig  # noqa: E402
from vuln_scan.sandbox import ExecResult  # noqa: E402

from backend.ci import private_ci_controller as pcc  # noqa: E402
from backend.ci.private_sandbox_executor import PrivateSandboxExecutor  # noqa: E402

PR_HEAD_SHA, MERGE_QUEUE_SHA = "a" * 40, "b" * 40
IMAGE_DIGEST, SIGNING_KEY = "sha256:" + ("d" * 64), b"s" * 32
GATES = [{"id": "lint", "tool": "ruff", "version": "0.6.9", "command": ["ruff", "check", "."]}]


class FakeDockerRunner:
    def __init__(self, *, gate_rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.gate_rc = gate_rc

    def __call__(self, argv: Sequence[str], timeout: float, cap: int, env: Mapping[str, str]) -> ExecResult:
        cmd = list(argv)
        self.calls.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return ExecResult(0, f"{IMAGE_DIGEST} null", "")
        if cmd[:2] == ["docker", "exec"] and "kill" not in cmd:
            return ExecResult(self.gate_rc, "secret-internal-gate-log-example-corp", "")
        return ExecResult(0, "", "")


def _make_key_provider(tmp_path: Path, *, enabled: bool = True) -> KeyWrappingProvider:
    key_file = tmp_path / "vault.key"
    key_file.write_bytes(b"k" * 32)
    os.chmod(key_file, 0o600)
    pm_cfg = PrivateModeConfig(enabled=enabled, default_level=Level.SENSITIVE if enabled else Level.PUBLIC)
    return KeyWrappingProvider(
        backend="vault", private_mode=pm_cfg, env={"RUNNER_DASHBOARD_VAULT_KEY_PATH": str(key_file)}
    )


def test_github_payload_carries_only_state_and_opaque_id_at_pr_head_and_merge_queue(tmp_path: Path) -> None:
    """Controller runs manifest at PR head and merge-queue SHA; GitHub payload has only state + opaque id."""
    published: list[dict[str, str]] = []
    pm_cfg = PrivateModeConfig(enabled=True, default_level=Level.SENSITIVE)
    controller = pcc.PrivateCIController(
        PrivateSandboxExecutor(runner=FakeDockerRunner(gate_rc=0), work_root=tmp_path),
        pcc.PrivateCIStatusPublisher(published.append, SIGNING_KEY, pm_cfg),
        pcc.PrivateCIArtifactStore(tmp_path / "artifacts", _make_key_provider(tmp_path, enabled=True)),
        SIGNING_KEY,
        GATES,
    )
    head_res = controller.run_target("run001", "example-corp/widgets", PR_HEAD_SHA, "pr_head", True, now=1000.0)
    mq_res = controller.run_target("run002", "example-corp/widgets", MERGE_QUEUE_SHA, "merge_queue", True, now=1005.0)
    assert head_res.ran and head_res.state == "success" and mq_res.ran and mq_res.state == "success"
    assert len(published) == 2
    for item in published:
        assert set(item.keys()) == {"state", "id"} and item["state"] == "success" and item["id"].startswith("opaque:")
        assert "example-corp" not in str(item) and "secret-internal-gate-log" not in str(item)

    with pytest.raises(ClassificationError, match="SoD|sandbox credential"):
        pcc.PrivateCIStatusPublisher(published.append, SIGNING_KEY, pm_cfg, {"GITHUB_TOKEN": "forbidden"})


def test_forged_or_mismatched_attestation_rejected() -> None:
    """Forged signature or mismatched repo, SHA, target_kind, manifest, or environment is rejected."""
    published: list[dict[str, str]] = []
    pm_cfg = PrivateModeConfig(enabled=True, default_level=Level.SENSITIVE)
    publisher = pcc.PrivateCIStatusPublisher(published.append, SIGNING_KEY, pm_cfg)
    m_dig, e_dig = pcc.compute_manifest_digest(GATES), pcc.compute_environment_digest(IMAGE_DIGEST)
    att = pcc.create_attestation(
        "run003", "example-corp/widgets", PR_HEAD_SHA, "pr_head", m_dig, e_dig, "success", SIGNING_KEY
    )
    for tampered, kwargs in (
        (dataclasses.replace(att, signature="0" * 64), {}),
        (att, {"expected_repo": "example-corp/other"}),
        (att, {"expected_sha": MERGE_QUEUE_SHA}),
        (att, {"expected_target_kind": "merge_queue"}),
        (att, {"expected_manifest_digest": "f" * 64}),
        (att, {"expected_environment_digest": "e" * 64}),
    ):
        with pytest.raises(ClassificationError, match="attestation"):
            publisher.publish(
                tampered,
                kwargs.get("expected_repo", "example-corp/widgets"),
                kwargs.get("expected_sha", PR_HEAD_SHA),
                kwargs.get("expected_target_kind", "pr_head"),
                kwargs.get("expected_manifest_digest", m_dig),
                kwargs.get("expected_environment_digest", e_dig),
            )
    assert published == []


def test_encrypted_local_artifacts_and_ttl_gc(tmp_path: Path) -> None:
    """Artifacts are stored encrypted locally (no plaintext on disk) and TTL GC removes expired files."""
    store = pcc.PrivateCIArtifactStore(tmp_path / "artifacts", _make_key_provider(tmp_path, enabled=True))
    rec_exp = store.store_artifact("run-exp", {"gate": "lint", "output": "sensitive-artifact-body"}, 60.0, 1000.0)
    store.store_artifact("run-live", {"gate": "tests", "output": "still-valid-artifact"}, 600.0, 1000.0)
    assert "sensitive-artifact-body" not in rec_exp.path.read_text(encoding="utf-8")
    assert store.read_artifact("run-exp", now=1030.0)["output"] == "sensitive-artifact-body"

    with pytest.raises(ClassificationError, match="expired"):
        store.read_artifact("run-exp", now=1061.0)
    assert store.gc_expired(now=1061.0) == ["run-exp"] and not rec_exp.path.exists()
    assert store.read_artifact("run-live", now=1061.0)["output"] == "still-valid-artifact"


def test_flag_off_behaviour_unchanged(tmp_path: Path) -> None:
    """When private_ci is off, controller does not execute gates, write artifacts, or publish status."""
    published: list[dict[str, str]] = []
    runner = FakeDockerRunner()
    controller = pcc.PrivateCIController(
        PrivateSandboxExecutor(runner=runner, work_root=tmp_path),
        pcc.PrivateCIStatusPublisher(published.append, SIGNING_KEY),
        pcc.PrivateCIArtifactStore(tmp_path / "artifacts", _make_key_provider(tmp_path, enabled=False)),
        SIGNING_KEY,
        GATES,
    )
    res = controller.run_target("run004", "example-corp/widgets", PR_HEAD_SHA, "pr_head", False)
    assert res.enabled is False and res.ran is False and runner.calls == [] and published == []
