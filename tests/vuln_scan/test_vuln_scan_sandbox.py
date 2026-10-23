"""Phase-B sandbox: container hardening, argv-only exec, output caps (#2572)."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence

import pytest
from vuln_scan.sandbox import (
    MAX_OUTPUT_BYTES,
    SANDBOX_USER,
    ExecResult,
    Sandbox,
    SandboxError,
    container_name,
    exec_argv,
    image_tag,
    run_argv,
    subprocess_runner,
    validate_argv,
)

CONTAINER = container_name("abcd1234ef567890")
IMAGE = image_tag("abcd1234ef567890", "0bb24108aa" * 4)


class FakeDocker:
    def __init__(self, result: ExecResult | None = None) -> None:
        self.calls: list[tuple[list[str], float, int]] = []
        self.result = result or ExecResult(0, "out", "")

    def __call__(self, argv: Sequence[str], timeout: float, cap: int, env: Mapping[str, str]) -> ExecResult:
        self.calls.append((list(argv), timeout, cap))
        return self.result


def test_run_argv_is_fully_hardened() -> None:
    argv = run_argv(CONTAINER, IMAGE)
    for flag in (
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
    ):
        assert flag in argv
    assert argv[argv.index("--user") + 1] == SANDBOX_USER
    assert any(a.startswith("--pids-limit=") for a in argv)
    assert any(a.startswith("--memory=") for a in argv)
    tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    assert all("size=" in t and "nosuid" in t for t in tmpfs)
    assert any(t.startswith("/var/tmp") and "noexec" in t for t in tmpfs)
    joined = " ".join(argv)
    for forbidden in ("--privileged", "-v ", "--volume", "--mount", "docker.sock", "--network=host"):
        assert forbidden not in joined


def test_names_stay_in_the_scan_namespace() -> None:
    with pytest.raises(SandboxError):
        run_argv("runner-1", IMAGE)
    with pytest.raises(SandboxError):
        run_argv(CONTAINER, "ubuntu:latest")
    with pytest.raises(SandboxError):
        container_name("../../etc")


@pytest.mark.parametrize(
    "argv",
    [[], "ls -la", ["--privileged"], ["-v", "/:/host"], [1, 2], ["ls", "a\x00b"], ["x"] * 65, [" "]],
)
def test_validate_argv_rejects(argv: object) -> None:
    with pytest.raises(SandboxError):
        validate_argv(argv)


def test_exec_argv_puts_agent_argv_after_the_container() -> None:
    argv = exec_argv(CONTAINER, ["sh", "-c", "curl http://169.254.169.254"], timeout_s=30)
    idx = argv.index(CONTAINER)
    # Everything before the container is ours; the agent's argv is only the command.
    assert argv[:idx] == ["docker", "exec", "--user", SANDBOX_USER, "--env", "HOME=/tmp/home"]
    assert argv[idx + 1 : idx + 5] == ["timeout", "-s", "KILL", "30"]
    assert argv[idx + 5 :] == ["sh", "-c", "curl http://169.254.169.254"]


def test_negative_probes_never_touch_the_host() -> None:
    """A host-file read, a host shell and a network call all run inside the container."""
    docker = FakeDocker()
    box = Sandbox(CONTAINER, docker, env={})
    box.read("/etc/passwd")
    box.exec(["/bin/sh", "-c", "cat ~/.ssh/id_ed25519"])
    box.exec(["curl", "https://example.com"])
    for argv, _, _ in docker.calls:
        assert argv[:2] == ["docker", "exec"] and CONTAINER in argv
    with pytest.raises(SandboxError):
        box.read("relative/path")


def test_timeout_is_capped_and_kills_the_tree() -> None:
    docker = FakeDocker(ExecResult(137, "", "", timed_out=True))
    box = Sandbox(CONTAINER, docker, env={}, exec_timeout_s=60)
    box.exec(["sleep", "9999"], timeout_s=99999)
    first, timeout, _ = docker.calls[0]
    assert first[first.index("KILL") + 1] == "60" and timeout == 75
    assert docker.calls[1][0][-3:] == ["kill", "-9", "-1"]


def test_read_honours_max_bytes() -> None:
    docker = FakeDocker()
    Sandbox(CONTAINER, docker, env={}).read("/src/a.py", offset=10, max_bytes=100)
    argv, _, cap = docker.calls[0]
    assert argv[-5:] == ["tail", "-c", "+11", "--", "/src/a.py"] and cap == 100
    with pytest.raises(SandboxError):
        Sandbox(CONTAINER, docker, env={}).read("/src/a.py", max_bytes=10**9)


def test_subprocess_runner_caps_output_and_times_out() -> None:
    big = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 300000)"]
    result = subprocess_runner(big, 30, MAX_OUTPUT_BYTES, dict(os.environ))
    assert len(result.stdout) == MAX_OUTPUT_BYTES and result.truncated
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = subprocess_runner(slow, 0.5, 1024, dict(os.environ))
    assert result.timed_out
