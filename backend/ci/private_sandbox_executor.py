"""Shared ephemeral sandbox executor for Private Mode CI gates (issue #3266, PM-15).

Extracted from and reusing the ``vuln_scan`` container hardening primitives
(:mod:`vuln_scan.sandbox`, :mod:`vuln_scan.docker_ops`) without modifying the
``vuln_scan`` package (#2566):
- Ephemeral container and scratch directory per job, always torn down in ``finally``
- No host mounts, no ``VOLUME`` declarations, no privileged flags, no reusable credentials
- Documented phase-A egress allowlist (:data:`EGRESS_ALLOWLIST_HOSTS`) and strict
  ``--network=none`` zero-egress enforcement during phase-B gate execution
- Read-only rootfs and read-only toolchain layer (:data:`TOOLCHAIN_BIN`)
- Safe-disable contract under the ``private_ci`` feature flag
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ci.private_gate_manifest import load_manifest
from vuln_scan.docker_ops import docker_ids, image_digest, inspect_argv
from vuln_scan.sandbox import (
    MAX_OUTPUT_BYTES,
    SANDBOX_HOME,
    SANDBOX_USER,
    ExecResult,
    Runner,
    SandboxError,
    subprocess_runner,
    validate_argv,
)
from vuln_scan.watch import ScanAborted

SANDBOX_LABEL = "fleet.private_ci.sandbox=1"
TOOLCHAIN_ROOT = "/opt/toolchain"
TOOLCHAIN_BIN = "/opt/toolchain/bin"
EGRESS_ALLOWLIST_HOSTS: frozenset[str] = frozenset(
    {
        "pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "codeload.github.com",
        "ghcr.io",
    }
)
_JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,48}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FORBIDDEN_DOCKER_SUBSTRINGS = (
    "-v",
    "--volume",
    "--mount",
    "--privileged",
    "--network=host",
    "--net=host",
    "docker.sock",
    "/home/",
    "/root",
    "/etc/",
)
_CRED_KEY_RE = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY|CREDENTIAL|AUTH|COOKIE|SESSION|AWS_|GH_|GITHUB_|ANTHROPIC_|OPENAI_|VAULT_)",
    re.IGNORECASE,
)
_SAFE_ENV_KEYS: frozenset[str] = frozenset({"CI", "LANG", "LC_ALL", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE", "TZ"})


@dataclass(frozen=True)
class SandboxJobSpec:
    """Validated specification for a single ephemeral private-CI sandbox job."""

    job_id: str
    repo: str
    sha: str
    env: Mapping[str, str] = field(default_factory=dict)
    extra_docker_args: tuple[str, ...] = ()
    timeout_s: int = 600


@dataclass(frozen=True)
class SandboxJobResult:
    """Outcome of a private-CI sandbox job execution."""

    enabled: bool
    ran: bool
    exit_code: int
    gate_results: tuple[tuple[str, ExecResult], ...] = ()
    image_digest: str = ""
    cleaned_up: bool = True


def is_private_ci_enabled(env: Mapping[str, str] | None = None, *, enabled: bool | None = None) -> bool:
    """Return True only when the ``private_ci`` flag is explicitly enabled."""
    if enabled is not None:
        return bool(enabled)
    source = os.environ if env is None else env
    return source.get("PRIVATE_CI", "").strip().lower() in {"1", "true", "yes"}


def container_name(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise SandboxError(f"invalid sandbox job_id: {job_id!r}")
    return f"pci-{job_id}"


def image_tag(job_id: str, sha: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise SandboxError(f"invalid sandbox job_id: {job_id!r}")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise SandboxError(f"invalid commit sha: {sha!r}")
    return f"pci-job/{job_id}:{sha[:12]}"


def verify_egress_target(url_or_host: str, *, phase: str = "exec") -> str:
    """Verify network egress target against phase-B zero-egress and phase-A registry allowlist."""
    if phase == "exec":
        raise SandboxError("phase-B sandbox execution denies all network egress (--network=none)")
    if phase not in {"fetch", "build"}:
        raise SandboxError(f"unsupported sandbox phase for egress check: {phase!r}")
    if not isinstance(url_or_host, str) or not url_or_host.strip():
        raise SandboxError("egress target must be a non-empty string")
    raw = url_or_host.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme != "https":
            raise SandboxError("phase-A egress requires https scheme")
        host = (parsed.hostname or "").lower()
    else:
        host = raw.split("/", 1)[0].split(":", 1)[0].lower()
    if not host or host == "localhost":
        raise SandboxError("egress to localhost or empty host is denied")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        raise SandboxError("egress to raw IP literal is denied")
    if host not in EGRESS_ALLOWLIST_HOSTS:
        raise SandboxError(f"egress host {host!r} is not in EGRESS_ALLOWLIST_HOSTS")
    return host


def validate_job_spec(spec: SandboxJobSpec) -> SandboxJobSpec:
    """Validate job spec preconditions: no host mounts, no privileged flags, no reusable creds."""
    if not isinstance(spec, SandboxJobSpec):
        raise SandboxError("spec must be a SandboxJobSpec")
    container_name(spec.job_id)
    image_tag(spec.job_id, spec.sha)
    if not isinstance(spec.repo, str) or not _REPO_RE.match(spec.repo) or ".." in spec.repo:
        raise SandboxError("invalid repo identifier")
    if not isinstance(spec.timeout_s, int) or not 1 <= spec.timeout_s <= 3600:
        raise SandboxError("timeout_s must be between 1 and 3600")
    for arg in spec.extra_docker_args:
        if not isinstance(arg, str):
            raise SandboxError("extra_docker_args items must be strings")
        lowered = arg.strip().lower()
        if any(tok in lowered for tok in _FORBIDDEN_DOCKER_SUBSTRINGS) or ":" in lowered:
            raise SandboxError(f"forbidden docker flag or host mount in sandbox spec: {arg!r}")
    for key, val in spec.env.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise SandboxError("env keys and values must be strings")
        if _CRED_KEY_RE.search(key) or key not in _SAFE_ENV_KEYS:
            raise SandboxError(f"reusable credential or non-allowlisted env var refused: {key!r}")
        if any(p in val for p in ("ghp_", "ghs_", "github_pat_", "sk-ant-", "/home/", "/root")):
            raise SandboxError(f"credential or host path value refused in env {key!r}")
    return spec


def build_run_argv(spec: SandboxJobSpec) -> list[str]:
    """Construct hardened ``docker run`` argv for an ephemeral private-CI container."""
    validate_job_spec(spec)
    cname = container_name(spec.job_id)
    img = image_tag(spec.job_id, spec.sha)
    argv = [
        "docker",
        "run",
        "--detach",
        "--name",
        cname,
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--pids-limit=512",
        "--memory=4g",
        "--memory-swap=4g",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=2048m",
        "--tmpfs",
        "/var/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--user",
        SANDBOX_USER,
        "--env",
        f"HOME={SANDBOX_HOME}",
        "--env",
        f"PATH={TOOLCHAIN_BIN}:/usr/local/bin:/usr/bin:/bin",
        "--label",
        SANDBOX_LABEL,
    ]
    for k, v in sorted(spec.env.items()):
        argv.extend(["--env", f"{k}={v}"])
    argv.extend(["--entrypoint", "sleep", img, "infinity"])
    return argv


def build_exec_argv(cname: str, command: Sequence[str], *, timeout_s: int) -> list[str]:
    validated = validate_argv(list(command))
    return [
        "docker",
        "exec",
        "--user",
        SANDBOX_USER,
        "--env",
        f"HOME={SANDBOX_HOME}",
        "--env",
        f"PATH={TOOLCHAIN_BIN}:/usr/local/bin:/usr/bin:/bin",
        cname,
        "timeout",
        "-s",
        "KILL",
        str(timeout_s),
        *validated,
    ]


class PrivateSandboxExecutor:
    """Ephemeral per-job sandbox executor for private CI quality gates."""

    def __init__(
        self,
        *,
        runner: Runner = subprocess_runner,
        work_root: Path | None = None,
        host_env: Mapping[str, str] | None = None,
    ) -> None:
        self._run = runner
        self._work_root = work_root
        base = os.environ if host_env is None else host_env
        self._docker_env = {k: v for k, v in base.items() if k in {"PATH", "HOME", "SYSTEMROOT", "DOCKER_HOST"}}

    def _teardown(self, cname: str, img: str, scratch: Path | None) -> None:
        self._run(["docker", "exec", "--user", SANDBOX_USER, cname, "kill", "-9", "-1"], 15, 4096, self._docker_env)
        self._run(["docker", "rm", "-f", "-v", cname], 30, 4096, self._docker_env)
        self._run(["docker", "rmi", "-f", img], 30, 4096, self._docker_env)
        for list_cmd, rm_prefix in (
            (["docker", "ps", "-aq", "--filter", f"label={SANDBOX_LABEL}"], ["docker", "rm", "-f", "-v"]),
            (["docker", "image", "ls", "-q", "--filter", f"label={SANDBOX_LABEL}"], ["docker", "rmi", "-f"]),
        ):
            out = self._run(list_cmd, 30, 4096, self._docker_env)
            for obj_id in docker_ids(out.stdout):
                self._run([*rm_prefix, obj_id], 30, 4096, self._docker_env)
        if scratch is not None and scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)

    def run_job(
        self,
        spec: SandboxJobSpec,
        *,
        gates: Sequence[Mapping[str, Any]] | None = None,
        enabled: bool | None = None,
    ) -> SandboxJobResult:
        """Run private CI gates inside an ephemeral container; tear down unconditionally."""
        validate_job_spec(spec)
        if not is_private_ci_enabled(enabled=enabled):
            return SandboxJobResult(enabled=False, ran=False, exit_code=0, cleaned_up=True)
        resolved_gates: Sequence[Mapping[str, Any]] = list(gates) if gates is not None else load_manifest()
        cname = container_name(spec.job_id)
        img = image_tag(spec.job_id, spec.sha)
        scratch: Path | None = None
        try:
            scratch = Path(tempfile.mkdtemp(prefix=f"{cname}-", dir=self._work_root))
            insp = self._run(inspect_argv(img), 30, 4096, self._docker_env)
            try:
                digest = image_digest(insp.stdout)
            except (RuntimeError, ScanAborted) as exc:
                raise SandboxError(f"sandbox image rejected: {exc}") from exc
            start_res = self._run(build_run_argv(spec), 60, 4096, self._docker_env)
            if start_res.exit_code != 0:
                raise SandboxError(f"failed to start ephemeral sandbox container {cname}")
            results: list[tuple[str, ExecResult]] = []
            exit_code = 0
            for gate in resolved_gates:
                gid = str(gate["id"])
                res = self._run(
                    build_exec_argv(cname, gate["command"], timeout_s=spec.timeout_s),
                    spec.timeout_s + 15,
                    MAX_OUTPUT_BYTES,
                    self._docker_env,
                )
                results.append((gid, res))
                if res.exit_code != 0:
                    exit_code = res.exit_code
                    break
            return SandboxJobResult(
                enabled=True,
                ran=True,
                exit_code=exit_code,
                gate_results=tuple(results),
                image_digest=digest,
                cleaned_up=True,
            )
        finally:
            self._teardown(cname, img, scratch)
