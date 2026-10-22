from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

import pytest  # noqa: E402
from node_auto_deploy.releases import release_root  # noqa: E402
from node_auto_deploy.transaction import (  # noqa: E402
    UnsafeDeploymentPathError,
    activate_release,
    copy_mutable_state,
    deployment_lock,
    ensure_candidate_available,
    install_and_run_transaction,
    restore_previous_target,
)

_OLD = "a" * 40
_NEW = "b" * 40


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = True) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlink capability is unavailable: {exc}")


def _release(live: Path, sha: str) -> Path:
    path = live.parent / "dashboard-releases" / sha
    path.mkdir(parents=True)
    return path


def _staging(live: Path) -> Path:
    path = live.parent / "dashboard-releases" / ".staging-test"
    path.mkdir(parents=True)
    return path


def test_plain_live_migration_is_reversible_and_preserves_state(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    (live / "deployment.json").write_text('{"git_sha":"' + _OLD + '"}', encoding="utf-8")
    (live / "dispatch_audit.ndjson").write_text('{"event":"keep"}\n', encoding="utf-8")
    (live / "dispatch_arm_cap.sqlite3-wal").write_bytes(b"wal")
    env_file = live / ".env"
    env_file.write_text("PRIVATE_OPERATOR_SETTING=retained\n", encoding="utf-8")
    env_file.chmod(0o600)
    (live / "config").mkdir()
    (live / "config" / "operator.json").write_text('{"setting":true}', encoding="utf-8")
    (live / "backend" / "data").mkdir(parents=True)
    (live / "backend" / "data" / "session.jsonl").write_text("row\n", encoding="utf-8")
    (live / "backend" / "data" / "nested").mkdir()
    (live / "backend" / "data" / "nested" / "runs.sqlite3").write_bytes(b"db")
    (live / "backend" / "machine_registry.yml").write_text("operator registry\n", encoding="utf-8")

    candidate = _release(live, _NEW)
    (candidate / "backend").mkdir()
    (candidate / "backend" / "new_code.py").write_text("new app code", encoding="utf-8")
    (candidate / ".venv").mkdir()
    (candidate / ".venv" / "ignored.sqlite3").write_bytes(b"immutable")
    (candidate / "backend" / "data" / "new_runtime_code.py").parent.mkdir(parents=True, exist_ok=True)
    (candidate / "backend" / "data" / "new_runtime_code.py").write_text("artifact runtime code", encoding="utf-8")
    (candidate / "config").mkdir()
    (candidate / "config" / "default.json").write_text("new default", encoding="utf-8")

    copy_mutable_state(live, candidate)
    try:
        receipt = activate_release(live, candidate)
    except OSError as exc:
        pytest.skip(f"symlink capability is unavailable: {exc}")

    assert live.is_symlink() and live.resolve() == candidate.resolve()
    assert (live / "dispatch_audit.ndjson").read_text(encoding="utf-8") == '{"event":"keep"}\n'
    assert (live / "dispatch_arm_cap.sqlite3-wal").read_bytes() == b"wal"
    assert (live / ".env").read_text(encoding="utf-8") == "PRIVATE_OPERATOR_SETTING=retained\n"
    if os.name == "posix":
        assert stat.S_IMODE((live / ".env").stat().st_mode) == 0o600
    assert (live / "backend" / "data" / "nested" / "runs.sqlite3").read_bytes() == b"db"
    assert (live / "config" / "operator.json").is_file()
    assert (live / "config" / "default.json").is_file()
    assert (live / "backend" / "data" / "session.jsonl").is_file()
    assert (live / "backend" / "machine_registry.yml").read_text(encoding="utf-8") == "operator registry\n"
    assert (live / "backend" / "new_code.py").read_text(encoding="utf-8") == "new app code"
    assert (live / "backend" / "data" / "new_runtime_code.py").read_text(encoding="utf-8") == "artifact runtime code"
    assert (live / ".venv" / "ignored.sqlite3").read_bytes() == b"immutable"

    restore_previous_target(live, receipt)
    assert live.is_dir() and not live.is_symlink()
    assert (live / "config" / "operator.json").is_file()
    assert (live / "dispatch_arm_cap.sqlite3-wal").read_bytes() == b"wal"
    assert candidate.is_dir()


def test_existing_release_symlink_restores_exact_previous_release(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    old = _release(live, _OLD)
    new = _release(live, _NEW)
    _symlink_or_skip(live, old)

    receipt = activate_release(live, new)
    assert live.resolve() == new.resolve()
    restore_previous_target(live, receipt)
    assert live.resolve() == old.resolve()
    assert new.is_dir()


def test_existing_candidate_is_refused_without_removal(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    candidate = _release(live, _NEW)
    marker = candidate / "evidence.txt"
    marker.write_text("preserve", encoding="utf-8")
    actions: list[str] = []

    outcome = install_and_run_transaction(
        live,
        candidate,
        install_candidate=lambda _stage: actions.append("install"),
        check_idle=lambda: actions.append("idle") or True,
        check_quiesced=lambda: True,
        stop_service=lambda: actions.append("stop"),
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: True,
    )
    assert outcome.result == "failed"
    assert actions == []
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_legacy_migration_collision_is_refused_before_install(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    releases = live.parent / "dashboard-releases"
    legacy = releases / "legacy-pre-auto-deploy"
    legacy.mkdir(parents=True)
    marker = legacy / "prior-data.txt"
    marker.write_text("preserve", encoding="utf-8")
    actions: list[str] = []

    outcome = install_and_run_transaction(
        live,
        releases / _NEW,
        install_candidate=lambda _stage: actions.append("install"),
        check_idle=lambda: actions.append("idle") or True,
        check_quiesced=lambda: True,
        stop_service=lambda: actions.append("stop"),
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: True,
    )
    assert outcome.result == "failed"
    assert actions == []
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_external_live_symlink_is_rejected(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    outside = tmp_path / "outside"
    outside.mkdir()
    new = _release(live, _NEW)
    _symlink_or_skip(live, outside)

    with pytest.raises(UnsafeDeploymentPathError):
        activate_release(live, new)
    assert live.resolve() == outside.resolve()


def test_mutable_state_symlinks_are_not_followed(tmp_path: Path) -> None:
    source = tmp_path / "old"
    candidate = tmp_path / "new"
    source.mkdir()
    candidate.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    (source / "config").mkdir()
    _symlink_or_skip(source / "config" / "linked.json", outside, directory=False)

    with pytest.raises(UnsafeDeploymentPathError):
        copy_mutable_state(source, candidate)
    assert not (candidate / "config" / "linked.json").exists()


def test_candidate_env_symlink_is_rejected_without_overwriting_external_file(tmp_path: Path) -> None:
    source = tmp_path / "old"
    candidate = tmp_path / "new"
    source.mkdir()
    candidate.mkdir()
    (source / ".env").write_text("new secret\n", encoding="utf-8")
    outside = tmp_path / "outside.env"
    outside.write_text("existing external bytes\n", encoding="utf-8")
    _symlink_or_skip(candidate / ".env", outside, directory=False)

    with pytest.raises(UnsafeDeploymentPathError):
        copy_mutable_state(source, candidate)
    assert outside.read_text(encoding="utf-8") == "existing external bytes\n"


def test_uv_bootstrap_symlink_is_pruned_without_following(tmp_path: Path) -> None:
    source = tmp_path / "old"
    candidate = tmp_path / "new"
    source.mkdir()
    candidate.mkdir()
    outside = tmp_path / "outside-bootstrap"
    outside.mkdir()
    marker = outside / "bootstrap.sqlite3"
    marker.write_bytes(b"external bootstrap data")
    _symlink_or_skip(source / "uv-bootstrap", outside)

    copy_mutable_state(source, candidate)
    assert marker.read_bytes() == b"external bootstrap data"
    assert not (candidate / "uv-bootstrap").exists()


def test_candidate_must_be_in_release_root_and_not_symlink(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(UnsafeDeploymentPathError):
        ensure_candidate_available(live, outside)

    _release(live, _NEW)
    other = tmp_path / "other"
    other.mkdir()
    link = tmp_path / "dashboard-releases" / ("c" * 40)
    _symlink_or_skip(link, other)
    with pytest.raises(UnsafeDeploymentPathError):
        ensure_candidate_available(live, link)


def test_relative_live_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UnsafeDeploymentPathError):
        ensure_candidate_available(Path("relative-dashboard"), tmp_path / _NEW)


@pytest.mark.parametrize("unsafe_live", [Path.home(), Path(Path.cwd().anchor)])
def test_home_and_filesystem_root_live_paths_are_rejected(unsafe_live: Path) -> None:
    with pytest.raises(UnsafeDeploymentPathError):
        ensure_candidate_available(unsafe_live, release_root(unsafe_live) / _NEW)


def test_release_root_symlink_is_rejected(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    outside = tmp_path / "outside-releases"
    outside.mkdir()
    release_link = tmp_path / "dashboard-releases"
    _symlink_or_skip(release_link, outside)
    with pytest.raises(UnsafeDeploymentPathError, match="release root must not be a symlink"):
        ensure_candidate_available(live, release_link / _NEW)


def test_release_lock_refuses_overlapping_transaction(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    with deployment_lock(live):
        with pytest.raises(RuntimeError, match="another deployment owns"):
            with deployment_lock(live):
                pass
    assert not (tmp_path / "dashboard-releases" / ".node-auto-deploy.lock").exists()


def test_install_failure_keeps_live_state_and_never_stops_service(tmp_path: Path) -> None:
    live = tmp_path / "dashboard"
    live.mkdir()
    state = live / "config.json"
    state.write_text("keep", encoding="utf-8")
    candidate = live.parent / "dashboard-releases" / _NEW
    actions: list[str] = []

    def fail_install(staging: Path) -> None:
        (staging / "partial.txt").write_text("diagnostic", encoding="utf-8")
        raise RuntimeError("artifact install failed")

    outcome = install_and_run_transaction(
        live,
        candidate,
        fail_install,
        check_idle=lambda: actions.append("idle") or True,
        check_quiesced=lambda: True,
        stop_service=lambda: actions.append("stop"),
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: True,
    )
    assert outcome.result == "failed"
    assert "artifact install failed" in outcome.message
    assert actions == []
    assert state.read_text(encoding="utf-8") == "keep"
    assert not candidate.exists()
    assert list((tmp_path / "dashboard-releases").iterdir()) == []


def test_plain_live_is_restored_if_pointer_creation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import node_auto_deploy.transaction as transaction

    live = tmp_path / "dashboard"
    live.mkdir()
    marker = live / "operator-state.txt"
    marker.write_text("keep", encoding="utf-8")
    candidate = _release(live, _NEW)

    def fail_pointer(*_args: object) -> None:
        raise OSError("simulated link failure")

    monkeypatch.setattr(transaction, "_atomic_pointer", fail_pointer)
    with pytest.raises(OSError, match="simulated link failure"):
        activate_release(live, candidate)
    assert live.is_dir() and not live.is_symlink()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (live.parent / "dashboard-releases" / "legacy-pre-auto-deploy").exists()


def test_activity_recheck_blocks_stop(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    live.mkdir()
    candidate = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    actions: list[str] = []
    outcome = run_deployment_transaction(
        live,
        candidate,
        staged,
        check_idle=lambda: actions.append("idle") or False,
        check_quiesced=lambda: True,
        stop_service=lambda: actions.append("stop"),
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: True,
    )
    assert outcome.result == "skipped"
    assert actions == ["idle"]


def test_stop_failure_still_attempts_to_resume_old_service(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    live.mkdir()
    candidate = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    actions: list[str] = []

    def stop() -> None:
        actions.append("stop")
        raise RuntimeError("stop command returned failure after partial stop")

    outcome = run_deployment_transaction(
        live,
        candidate,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: True,
        stop_service=stop,
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: True,
    )
    assert outcome.result == "failed"
    assert actions == ["stop", "restart"]


def test_post_stop_activity_aborts_switch_and_resumes_old_service(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    live.mkdir()
    candidate = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    actions: list[str] = []
    outcome = run_deployment_transaction(
        live,
        candidate,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: actions.append("quiesced-check") or False,
        stop_service=lambda: actions.append("stop"),
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: actions.append("health") or True,
    )
    assert outcome.result == "failed"
    assert "activity remains after service stop" in outcome.message
    assert actions == ["stop", "quiesced-check", "restart", "health"]
    assert live.is_dir() and not live.is_symlink()
    assert not candidate.exists()


def test_restart_or_health_failure_restores_and_restarts_previous(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    old = _release(live, _OLD)
    new = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    _symlink_or_skip(live, old)
    actions: list[str] = []

    def restart() -> None:
        actions.append("restart")
        if actions.count("restart") == 1:
            raise RuntimeError("new service restart failed")

    outcome = run_deployment_transaction(
        live,
        new,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: True,
        stop_service=lambda: actions.append("stop"),
        restart_service=restart,
        healthy=lambda: True,
    )
    assert outcome.result == "rollback"
    assert live.resolve() == old.resolve()
    assert actions == ["stop", "restart", "stop", "restart"]


def test_health_failure_restores_and_restarts_previous(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    old = _release(live, _OLD)
    new = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    _symlink_or_skip(live, old)
    actions: list[str] = []
    health_results = iter([False, True])
    outcome = run_deployment_transaction(
        live,
        new,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: True,
        stop_service=lambda: actions.append("stop"),
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: next(health_results),
    )
    assert outcome.result == "rollback"
    assert live.resolve() == old.resolve()
    assert actions == ["stop", "restart", "stop", "restart"]


def test_unhealthy_previous_release_is_not_recorded_as_rollback(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    old = _release(live, _OLD)
    new = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    _symlink_or_skip(live, old)
    health_results = iter([False, False])
    outcome = run_deployment_transaction(
        live,
        new,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: True,
        stop_service=lambda: None,
        restart_service=lambda: None,
        healthy=lambda: next(health_results),
    )
    assert outcome.result == "rollback_failed"
    assert "previous release health check failed" in outcome.message
    assert live.resolve() == old.resolve()


def test_candidate_is_stopped_before_previous_state_is_restored(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    old = _release(live, _OLD)
    new = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    _symlink_or_skip(live, old)
    actions: list[str] = []
    health_results = iter([False, True])

    outcome = run_deployment_transaction(
        live,
        new,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: True,
        stop_service=lambda: actions.append("stop-candidate" if new.is_dir() else "stop-old"),
        restart_service=lambda: actions.append("restart"),
        healthy=lambda: next(health_results),
    )

    assert outcome.result == "rollback"
    assert actions == ["stop-old", "restart", "stop-candidate", "restart"]
    assert live.resolve() == old.resolve()


def test_candidate_stop_failure_does_not_restore_previous_pointer(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    old = _release(live, _OLD)
    new = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    _symlink_or_skip(live, old)
    stops = 0

    def stop() -> None:
        nonlocal stops
        stops += 1
        if stops == 2:
            raise RuntimeError("candidate stop failed")

    outcome = run_deployment_transaction(
        live,
        new,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: True,
        stop_service=stop,
        restart_service=lambda: None,
        healthy=lambda: False,
    )

    assert outcome.result == "rollback_failed"
    assert "candidate stop failed" in outcome.message
    assert live.resolve() == new.resolve()
    assert old.is_dir() and new.is_dir()


def test_failed_rollback_is_reported_distinctly_and_keeps_releases(tmp_path: Path) -> None:
    from node_auto_deploy.transaction import run_deployment_transaction

    live = tmp_path / "dashboard"
    old = _release(live, _OLD)
    new = live.parent / "dashboard-releases" / _NEW
    staged = _staging(live)
    _symlink_or_skip(live, old)
    calls = 0

    def restart() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("service restart failed")

    outcome = run_deployment_transaction(
        live,
        new,
        staged,
        check_idle=lambda: True,
        check_quiesced=lambda: True,
        stop_service=lambda: None,
        restart_service=restart,
        healthy=lambda: True,
    )
    assert outcome.result == "rollback_failed"
    assert live.resolve() == old.resolve()
    assert new.is_dir() and old.is_dir()
    assert calls == 2
