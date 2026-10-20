"""Dry-run previews must preserve runtime state, including failure paths (#2007)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path, docker_shim_prelude

CLEANUP = Path(__file__).resolve().parents[2] / "deploy" / "runner-cleanup.sh"
pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _snapshot(root: Path) -> dict[str, tuple[bytes | None, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }


def _seed_runtime(root: Path, existing: bool) -> None:
    locks = root / "busy"
    locks.mkdir(parents=True)
    (root / "proc").mkdir()
    for name, body in {"aged": "pid=10\n", "dead": "worker_pid=999999\n"}.items():
        lock = locks / f"{name}.lock"
        lock.write_text(body, encoding="utf-8")
        if name == "aged":
            old = time.time() - 3 * 86400
            os.utime(lock, (old, old))
    if existing:
        (root / "mutex").write_text("existing lock evidence\n", encoding="utf-8")
        (root / "metrics").mkdir()
        (root / "metrics/cleanup.prom").write_text("previous real run\n", encoding="utf-8")


def _harness(tmp_path: Path, fail: bool) -> Path:
    """Exercise real startup, main, busy GC and EXIT trap; stub host actions."""
    shims = tmp_path / "bin"
    shims.mkdir()
    (shims / "docker").write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8", newline="\n")
    (shims / "docker").chmod(0o755)
    text = CLEANUP.read_text(encoding="utf-8")
    text = text.replace(
        'LOCK_FILE="/run/runner-cleanup.lock"', f'LOCK_FILE="{as_bash_path(tmp_path / "runtime/mutex")}"'
    )
    stubs = [
        "flock() { return 0; }",
        "journalctl() { return 0; }",
        "fstrim() { return 0; }",
        "root_used_bytes() { echo 100; }",
        "root_used_percent() { echo 1; }",
        "cleanup_docker() { return 0; }",
        "cleanup_runners() { return 0; }",
        f"cleanup_tmp() {{ return {1 if fail else 0}; }}",
        "cleanup_runner_tmpdirs() { return 0; }",
        "cleanup_gitconfig_token_rewrites() { return 0; }",
        "cleanup_common_caches() { return 0; }",
        "cleanup_rustup_tmp() { return 0; }",
    ]
    text = text.replace('main "$@"', "\n".join(stubs) + '\nmain "$@"')
    script = tmp_path / "harness.sh"
    script.write_text(docker_shim_prelude(shims) + text, encoding="utf-8", newline="\n")
    return script


def _run(tmp_path: Path, *, dry_run: str, fail: bool = False) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    root = tmp_path / "runtime"
    env = os.environ | {
        "DRY_RUN": "1" if dry_run == "env" else "0",
        "LOG_DIR": as_bash_path(root / "logs"),
        "RUNNER_BUSY_LOCK_DIR": as_bash_path(root / "busy"),
        "PROC_ROOT": as_bash_path(root / "proc"),
        "CLEANUP_METRICS_FILE": as_bash_path(root / "metrics/cleanup.prom"),
        "COMPACT_VHD": "0",
        "COMPACT_VHD_ONLY": "0",
        "USER": "cleanup-test",
    }
    args = [BASH, as_bash_path(_harness(tmp_path, fail)), "--disk-guard"]
    if dry_run == "flag":
        args.append("--dry-run")
    return subprocess.run(args, env=env, capture_output=True, text=True, check=False, timeout=30)


@pytest.mark.parametrize("dry_run", ["flag", "env"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_dry_run_preserves_runtime_state(tmp_path: Path, dry_run: str, existing: bool, fail: bool) -> None:
    root = tmp_path / "runtime"
    _seed_runtime(root, existing)
    before = _snapshot(root)
    result = _run(tmp_path, dry_run=dry_run, fail=fail)
    assert result.returncode == (1 if fail else 0), result.stdout + result.stderr
    assert _snapshot(root) == before, "preview must not create, truncate, remove or rewrite runtime state"


@pytest.mark.parametrize("fail", [False, True])
def test_real_run_still_reaps_locks_and_publishes_metrics(tmp_path: Path, fail: bool) -> None:
    root = tmp_path / "runtime"
    _seed_runtime(root, existing=False)
    result = _run(tmp_path, dry_run="live", fail=fail)
    assert result.returncode == (1 if fail else 0), result.stdout + result.stderr
    assert list((root / "busy").iterdir()) == []
    assert (root / "mutex").exists()
    assert (root / "logs").is_dir()
    outcome = "failed" if fail else "ok"
    assert f'result="{outcome}"}} 1' in (root / "metrics/cleanup.prom").read_text(encoding="utf-8")


@pytest.mark.parametrize("dry_run", ["0", "1"])
def test_runner_preview_preserves_restart_marker(tmp_path: Path, dry_run: str) -> None:
    runner = tmp_path / "runner-1"
    runner.mkdir()
    marker = runner / ".restart-required"
    marker.write_text("pending restart\n", encoding="utf-8")
    text = CLEANUP.read_text(encoding="utf-8")
    functions = []
    for name in ("run", "cleanup_runners"):
        start = text.index(f"{name}() {{")
        functions.append(text[start : text.index("\n}\n", start) + 3])
    root = as_bash_path(tmp_path)
    script = tmp_path / "restart.sh"
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                f"DRY_RUN={dry_run}",
                f'RUNNER_ROOTS="{root}"',
                "log() { :; }",
                "list_runner_units() { echo fake.service; }",
                f'service_workdir() {{ echo "{as_bash_path(runner)}"; }}',
                "unit_active() { return 0; }",
                "runner_busy() { return 1; }",
                "systemctl() { return 0; }",
                "cleanup_runner_workdir() { :; }",
                "cleanup_runner_diag() { :; }",
                *functions,
                "cleanup_runners",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    assert BASH is not None
    result = subprocess.run([BASH, as_bash_path(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists() is (dry_run == "1")
