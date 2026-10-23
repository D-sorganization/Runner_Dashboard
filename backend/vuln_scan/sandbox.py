"""Docker sandbox for security scans: phase-B container and bounded exec (#2572).

Panel verdict q4: *agent outside, code inside*. The scanned repository only ever
runs inside a container started by :func:`run_argv`:

- ``--network=none``, ``--cap-drop=ALL``, ``no-new-privileges``, a read-only rootfs
- pids and memory caps, size-capped tmpfs mounts, and no host mounts
- a fixed unprivileged uid (:data:`SANDBOX_USER`)

The agent reaches it only through :class:`Sandbox`. Each call is a
``docker exec`` of an argv list, never a shell string, as uid 65532 and bound to
one container. Output is capped while it streams; a timeout kills the process
tree inside the container (``kill -9 -1`` as the sandbox user), not just the
local docker client.

Docker is reached through an injected :data:`Runner`, so every rule here is
tested with a fake docker and no daemon.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO

SANDBOX_UID = 65532
SANDBOX_USER = f"{SANDBOX_UID}:{SANDBOX_UID}"
SANDBOX_HOME = "/tmp/home"  # noqa: S108 - inside the container's tmpfs
MAX_ARGV = 64
MAX_ARG_CHARS = 4096
MAX_OUTPUT_BYTES = 64 * 1024
MAX_READ_BYTES = 64 * 1024
_CONTAINER = re.compile(r"^vs-[a-z0-9-]{4,60}$")
_IMAGE = re.compile(r"^vs-scan/[a-z0-9-]{4,60}:[0-9a-f]{7,40}$")


class SandboxError(ValueError):
    """A request the sandbox refuses (bad argv, path or name)."""


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False
    timed_out: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "truncated": self.truncated,
            "timed_out": self.timed_out,
        }


# (argv, timeout_s, output_cap_bytes, env) -> ExecResult
Runner = Callable[[Sequence[str], float, int, Mapping[str, str]], ExecResult]


def container_name(scan_id: str) -> str:
    name = f"vs-{scan_id}"
    if not _CONTAINER.match(name):
        raise SandboxError(f"bad scan id {scan_id!r}")
    return name


def image_tag(scan_id: str, sha: str) -> str:
    tag = f"vs-scan/{scan_id}:{sha[:12]}"
    if not _IMAGE.match(tag):
        raise SandboxError(f"bad image tag for {scan_id!r}/{sha!r}")
    return tag


def run_argv(
    container: str,
    image: str,
    *,
    memory: str = "4g",
    pids: int = 512,
    tmp_mb: int = 2048,
) -> list[str]:
    """``docker run`` argv for the phase-B container (detached, idle PID 1)."""
    if not _CONTAINER.match(container) or not _IMAGE.match(image):
        raise SandboxError("container or image name outside the scan namespace")
    return [
        "docker",
        "run",
        "--detach",
        "--name",
        container,
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        f"--pids-limit={pids}",
        f"--memory={memory}",
        f"--memory-swap={memory}",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,size={tmp_mb}m",
        "--tmpfs",
        "/var/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--user",
        SANDBOX_USER,
        "--env",
        f"HOME={SANDBOX_HOME}",
        "--label",
        "fleet.security.scan=1",
        "--entrypoint",
        "sleep",
        image,
        "infinity",
    ]


def validate_argv(argv: object) -> list[str]:
    """Contract for ``sandbox_exec``: a non-empty list of plain strings."""
    if not isinstance(argv, list) or not argv:
        raise SandboxError("argv must be a non-empty list of strings")
    if len(argv) > MAX_ARGV:
        raise SandboxError(f"argv has more than {MAX_ARGV} items")
    for item in argv:
        if not isinstance(item, str):
            raise SandboxError("argv items must be strings")
        if len(item) > MAX_ARG_CHARS or "\x00" in item:
            raise SandboxError("argv item too long or contains NUL")
    if argv[0].startswith("-") or not argv[0].strip():
        raise SandboxError("argv[0] must name a program, not a flag")
    return list(argv)


def validate_path(path: object) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise SandboxError("path must be an absolute path inside the sandbox")
    if len(path) > MAX_ARG_CHARS or "\x00" in path:
        raise SandboxError("path too long or contains NUL")
    return path


def exec_argv(container: str, argv: Sequence[str], *, timeout_s: int) -> list[str]:
    """``docker exec`` argv: fixed user, no stdin/tty, in-container timeout.

    Everything after the container name is the command, so the agent's argv can
    never become a docker flag; ``timeout`` makes the kernel inside the container
    enforce the limit even if the docker client is killed.
    """
    if not _CONTAINER.match(container):
        raise SandboxError("container outside the scan namespace")
    return [
        "docker",
        "exec",
        "--user",
        SANDBOX_USER,
        "--env",
        f"HOME={SANDBOX_HOME}",
        container,
        "timeout",
        "-s",
        "KILL",
        str(timeout_s),
        *argv,
    ]


class Sandbox:
    """The only path from the agent to the code under scan; bound to one container."""

    def __init__(
        self,
        container: str,
        runner: Runner,
        *,
        env: Mapping[str, str],
        exec_timeout_s: int = 600,
    ) -> None:
        if not _CONTAINER.match(container):
            raise SandboxError("container outside the scan namespace")
        self.container = container
        self._run = runner
        self._env = dict(env)
        self.exec_timeout_s = exec_timeout_s

    def exec(self, argv: object, timeout_s: int | None = None) -> ExecResult:
        return self._exec(validate_argv(argv), timeout_s, MAX_OUTPUT_BYTES)

    def _exec(self, command: list[str], timeout_s: int | None, cap: int) -> ExecResult:
        limit = min(timeout_s or self.exec_timeout_s, self.exec_timeout_s)
        result = self._run(
            exec_argv(self.container, command, timeout_s=limit),
            limit + 15,
            cap,
            self._env,
        )
        if result.timed_out or result.exit_code == 137:
            self.kill_tree()
        return result

    def read(self, path: object, offset: int = 0, max_bytes: int = 16384) -> ExecResult:
        target = validate_path(path)
        if offset < 0 or not 1 <= max_bytes <= MAX_READ_BYTES:
            raise SandboxError(f"offset >= 0 and 1 <= max_bytes <= {MAX_READ_BYTES}")
        return self._exec(["tail", "-c", f"+{offset + 1}", "--", target], 60, max_bytes)

    def listdir(self, path: object) -> ExecResult:
        return self._exec(
            ["ls", "-la", "--", validate_path(path)], 60, MAX_OUTPUT_BYTES
        )

    def kill_tree(self) -> None:
        """Kill every sandbox-user process except PID 1 (``kill -1`` semantics)."""
        argv = ["docker", "exec", "--user", SANDBOX_USER, self.container]
        self._run([*argv, "kill", "-9", "-1"], 30, 4096, self._env)


def _drain(stream: IO[bytes], cap: int, sink: list[bytes], over: list[bool]) -> None:
    kept = 0
    for chunk in iter(lambda: stream.read(8192), b""):
        room = cap - kept
        if room > 0:
            sink.append(chunk[:room])
            kept += min(len(chunk), room)
        if len(chunk) > room:
            over[0] = True  # keep draining so the child never blocks on a full pipe
    stream.close()


def subprocess_runner(
    argv: Sequence[str], timeout_s: float, cap: int, env: Mapping[str, str]
) -> ExecResult:
    """Real :data:`Runner`: streams output, keeps ``cap`` bytes per stream."""
    proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
    )
    out: list[bytes] = []
    err: list[bytes] = []
    over = [False]
    assert proc.stdout is not None and proc.stderr is not None  # noqa: S101
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, cap, out, over)),
        threading.Thread(target=_drain, args=(proc.stderr, cap, err, over)),
    ]
    for t in threads:
        t.daemon = True
        t.start()
    timed_out = False
    try:
        code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        code = proc.wait()
    for t in threads:
        t.join(timeout=5)
    return ExecResult(
        exit_code=code,
        stdout=b"".join(out).decode("utf-8", "replace"),
        stderr=b"".join(err).decode("utf-8", "replace"),
        truncated=over[0],
        timed_out=timed_out,
    )
