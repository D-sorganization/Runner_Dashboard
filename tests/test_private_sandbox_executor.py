"""Contract tests for Private Mode PM-15 shared sandbox executor (issue #3266, epic #3249)."""

from __future__ import annotations

import datetime as _dt_mod
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

from vuln_scan.sandbox import ExecResult, SandboxError  # noqa: E402

from backend.ci import private_sandbox_executor as pse  # noqa: E402

SHA = "a1b2c3d4e5f6" * 3 + "a1b2"
DIGEST = "sha256:" + ("d" * 64)


class FakeDockerRunner:
    def __init__(self, *, inspect_out: str = f"{DIGEST} null", gate_rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.inspect_out = inspect_out
        self.gate_rc = gate_rc

    def __call__(self, argv: Sequence[str], timeout: float, cap: int, env: Mapping[str, str]) -> ExecResult:
        cmd = list(argv)
        self.calls.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return ExecResult(0, self.inspect_out, "")
        if cmd[:2] == ["docker", "ps"] or cmd[:3] == ["docker", "image", "ls"]:
            return ExecResult(0, "", "")
        if cmd[:2] == ["docker", "exec"] and "kill" not in cmd:
            return ExecResult(self.gate_rc, "ok", "")
        return ExecResult(0, "", "")


def test_escape_fixtures_host_path_or_cred_env_fail() -> None:
    """Escape fixtures (host path mounts, docker.sock, volume flags, or credential env) fail closed."""
    for bad_flag in (
        ["-v", "/home/operator:/mnt"],
        ["--volume=/etc/shadow:/etc/shadow"],
        ["--mount", "type=bind,source=/,target=/host"],
        ["-v", "/var/run/docker.sock:/var/run/docker.sock"],
        ["--privileged"],
        ["--network=host"],
    ):
        with pytest.raises(SandboxError, match="host mount|forbidden"):
            pse.validate_job_spec(
                pse.SandboxJobSpec(
                    job_id="job001",
                    repo="example-corp/widgets",
                    sha=SHA,
                    extra_docker_args=tuple(bad_flag),
                )
            )

    for bad_env in (
        {"GITHUB_TOKEN": "ghp_secret"},
        {"GH_TOKEN": "ghs_secret"},
        {"AWS_SECRET_ACCESS_KEY": "secret"},
        {"ANTHROPIC_API_KEY": "sk-ant"},
        {"DATABASE_PASSWORD": "pw"},
        {"PRIVATE_KEY_PEM": "pem"},
    ):
        with pytest.raises(SandboxError, match="credential|secret"):
            pse.validate_job_spec(
                pse.SandboxJobSpec(job_id="job001", repo="example-corp/widgets", sha=SHA, env=bad_env)
            )

    # Image declaring a VOLUME is refused before container start
    runner = FakeDockerRunner(inspect_out=f'{DIGEST} {{"/data":{{}}}}')
    executor = pse.PrivateSandboxExecutor(runner=runner)
    spec = pse.SandboxJobSpec(job_id="job001", repo="example-corp/widgets", sha=SHA)
    with pytest.raises(SandboxError, match="VOLUME"):
        executor.run_job(spec, gates=[{"id": "lint", "command": ["ruff", "check", "."]}], enabled=True)


def test_egress_deny_and_documented_allowlist() -> None:
    """Phase-B execution denies all egress; phase-A fetch only allows documented registries."""
    assert "pypi.org" in pse.EGRESS_ALLOWLIST_HOSTS
    assert "codeload.github.com" in pse.EGRESS_ALLOWLIST_HOSTS

    # Phase B ("exec") denies even allowlisted hosts
    with pytest.raises(SandboxError, match="egress"):
        pse.verify_egress_target("https://pypi.org/simple/", phase="exec")

    # Phase A ("fetch") allows documented registries over HTTPS, denies unknown/private/HTTP
    assert pse.verify_egress_target("https://pypi.org/simple/", phase="fetch") == "pypi.org"
    for blocked in (
        "https://evil.example.org/exfil",
        "http://pypi.org/simple/",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/internal",
        "https://100.64.0.1/tailnet",
        "https://localhost:8321/api",
    ):
        with pytest.raises(SandboxError, match="egress"):
            pse.verify_egress_target(blocked, phase="fetch")


def test_teardown_leaves_nothing_behind_and_readonly_toolchain(tmp_path: Path) -> None:
    """Ephemeral container/image and scratch dir are always torn down, even on gate failure."""
    work_root = tmp_path / "sandbox-work"
    work_root.mkdir()
    runner = FakeDockerRunner(gate_rc=1)
    executor = pse.PrivateSandboxExecutor(runner=runner, work_root=work_root)
    spec = pse.SandboxJobSpec(job_id="job002", repo="example-corp/widgets", sha=SHA)
    res = executor.run_job(
        spec,
        gates=[{"id": "lint", "command": ["ruff", "check", "."]}],
        enabled=True,
    )
    assert res.ran is True and res.exit_code == 1 and res.cleaned_up is True
    assert list(work_root.iterdir()) == []

    run_call = next(c for c in runner.calls if c[:2] == ["docker", "run"])
    assert "--network=none" in run_call and "--read-only" in run_call
    assert f"PATH={pse.TOOLCHAIN_BIN}:/usr/local/bin:/usr/bin:/bin" in run_call
    assert any(c[:4] == ["docker", "rm", "-f", "-v"] for c in runner.calls)
    assert any(c[:3] == ["docker", "rmi", "-f"] for c in runner.calls)


def test_flag_off_behaviour_unchanged(tmp_path: Path) -> None:
    """When private_ci is disabled (default), no containers or commands run."""
    runner = FakeDockerRunner()
    executor = pse.PrivateSandboxExecutor(runner=runner, work_root=tmp_path)
    spec = pse.SandboxJobSpec(job_id="job003", repo="example-corp/widgets", sha=SHA)
    res = executor.run_job(
        spec,
        gates=[{"id": "lint", "command": ["ruff", "check", "."]}],
        enabled=False,
    )
    assert res.enabled is False and res.ran is False and res.exit_code == 0
    assert runner.calls == []
