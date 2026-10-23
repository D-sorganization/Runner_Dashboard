"""Scan executor end to end with a fake docker and a fake claude CLI (#2572)."""

from __future__ import annotations

import io
import json
import tarfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from vuln_scan import executor as executor_mod
from vuln_scan.executor import Probes, ScanAborted, ScanExecutor, ScanRequest, extract_tarball
from vuln_scan.findings import FindingsStore
from vuln_scan.operator_config import parse_operator_config
from vuln_scan.sandbox import ExecResult
from vuln_scan.scans import ScanLog

SHA = "0bb24108aa" * 4
DIGEST = "sha256:" + "ab" * 32
NIGHT = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)  # 02:00 America/Los_Angeles
INIT = {
    "type": "system",
    "subtype": "init",
    "tools": [
        "mcp__scan_sandbox__report_finding",
        "mcp__scan_sandbox__sandbox_exec",
        "mcp__scan_sandbox__sandbox_list",
        "mcp__scan_sandbox__sandbox_read",
    ],
    "mcp_servers": [{"name": "scan_sandbox", "status": "connected"}],
}
FINDING = {
    "title": "Path traversal",
    "cwe": "CWE-22",
    "severity": "high",
    "confidence": "high",
    "path": "src/load.py",
    "symbol": "load",
    "root_cause": "joins user path",
}


def _config(**overrides: Any) -> Any:
    payload = {
        "repos": {"Tools": {"enabled": True}},
        "scan_hosts": {"scanvm": {"isolation": "vm", "docker_host": "ssh://scan@scanvm", "build_network": "vs-build"}},
        "executor": {"cli_version": "2.1.263"},
        "max_new_findings_per_scan": 2,
    }
    payload.update(overrides)
    return parse_operator_config(payload)


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], timeout: float, cap: int, env: Mapping[str, str]) -> ExecResult:
        self.calls.append(list(argv))
        if argv[1:3] == ["image", "inspect"]:
            return ExecResult(0, DIGEST + "\n", "")
        return ExecResult(0, "", "")

    def verbs(self) -> list[str]:
        return [" ".join(c[1:3]) for c in self.calls]


def _tool(name: str) -> dict[str, Any]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": {}}]}}


RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 1.25, "result": "done"}


class FakeProc:
    def __init__(self, lines: list[str], code: int = 0, hang: bool = False) -> None:
        self.killed = threading.Event()
        self._lines = lines
        self._hang = hang
        self.returncode: int | None = None
        self._code = code
        self.stdin = io.StringIO()
        self.stdout: Iterator[str] = self._stream()
        self.pid = 0

    def _stream(self) -> Iterator[str]:
        yield from self._lines
        if self._hang:
            self.killed.wait(30)
        self.returncode = self._code

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._code
        return self._code

    def poll(self) -> int | None:
        return self.returncode


class FakePopen:
    """docker build prints progress; claude writes findings and streams events."""

    def __init__(self, events: list[dict[str, Any]], findings: list[dict[str, Any]], hang: bool = False) -> None:
        self.events = events
        self.findings = findings
        self.hang = hang
        self.argvs: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.procs: list[FakeProc] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProc:
        self.argvs.append(argv)
        self.envs.append(kwargs["env"])
        if argv[0] == "docker":
            proc = FakeProc(["#1 [internal] load build definition\n", "#9 DONE\n"])
        else:
            cfg = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
            args = cfg["mcpServers"]["scan_sandbox"]["args"]
            out = Path(args[args.index("--findings-out") + 1])
            out.write_text("".join(json.dumps(f) + "\n" for f in self.findings), encoding="utf-8")
            proc = FakeProc([json.dumps(e) + "\n" for e in self.events], hang=self.hang)
        self.procs.append(proc)
        return proc


def _probes(**overrides: Any) -> Probes:
    values: dict[str, Any] = {
        "default_branch_sha": lambda repo: SHA,
        "contract": lambda repo: {"valid": True, "dockerfile": ".oss-scanner/Dockerfile"},
        "queued_jobs": lambda: 0,
        "free_gb": lambda host: 200.0,
        "oauth_ok": lambda: True,
        "cli_version": lambda: "2.1.263 (Claude Code)",
        "fetch_source": lambda repo, sha, dest: (dest / "README").write_text("x", encoding="utf-8"),
        "now": lambda: NIGHT,
    }
    values.update(overrides)
    return Probes(**values)


def _executor(
    tmp_path: Path, popen: FakePopen, docker: FakeDocker, config: Any = None, **probe_overrides: Any
) -> ScanExecutor:
    def kill(proc: Any) -> None:
        proc.killed.set()

    return ScanExecutor(
        config or _config(),
        _probes(**probe_overrides),
        ScanLog(tmp_path / "db.sqlite"),
        FindingsStore(tmp_path / "db.sqlite"),
        docker=docker,
        cli_path="claude",
        popen=popen,
        poll_s=0.0,
        work_root=tmp_path,
        kill_tree=kill,
    )


def _run(ex: ScanExecutor, dry_run: bool = False) -> dict[str, Any]:
    req = ScanRequest(repo="Tools", sha=SHA, dry_run=dry_run, requested_by="tester")
    row = ex.submit(req)
    if row["state"] != "running":
        return row
    return ex.execute(row["id"], req)


def _work_dirs(tmp_path: Path) -> list[Path]:
    return [p for p in tmp_path.iterdir() if p.name.startswith("vs-")]


def test_dry_run_plans_without_docker_or_cli(tmp_path: Path) -> None:
    docker, popen = FakeDocker(), FakePopen([], [])
    row = _run(_executor(tmp_path, popen, docker), dry_run=True)
    assert row["state"] == "planned" and row["reasons"] == []
    assert docker.calls == [] and popen.argvs == []


def test_refused_without_a_vm_host(tmp_path: Path) -> None:
    docker, popen = FakeDocker(), FakePopen([], [])
    config = _config(scan_hosts={"brick": {"isolation": "container"}})
    row = _run(_executor(tmp_path, popen, docker, config=config))
    assert row["state"] == "refused" and any("no scan host" in r for r in row["reasons"])
    assert docker.calls == [] and popen.argvs == []


def test_cli_version_drift_refuses(tmp_path: Path) -> None:
    docker, popen = FakeDocker(), FakePopen([], [])
    row = _run(_executor(tmp_path, popen, docker, cli_version=lambda: "2.1.300 (Claude Code)"))
    assert row["state"] == "refused" and "pinned" in row["reasons"][0]


def test_happy_path_records_findings_and_cleans_up(tmp_path: Path) -> None:
    docker = FakeDocker()
    findings = [FINDING, {**FINDING, "symbol": "b"}, {**FINDING, "symbol": "c"}, {"title": "bad"}]
    popen = FakePopen([INIT, _tool("mcp__scan_sandbox__sandbox_exec"), RESULT], findings)
    ex = _executor(tmp_path, popen, docker)
    row = _run(ex)
    assert row["state"] == "succeeded", row["error"]
    assert row["findings_new"] == 2 and row["findings_seen"] == 3  # cap 2; one invalid dropped
    assert row["cost_usd"] == 1.25 and row["turns"] == 1
    stored = ex.store.list(repo="Tools")
    assert {f["image_digest"] for f in stored} == {DIGEST} and {f["scan_sha"] for f in stored} == {SHA}
    build = popen.argvs[0]
    assert build[:3] == ["docker", "buildx", "build"] and "--load" in build
    assert build[build.index("--builder") + 1] == "vs-build-builder"
    assert popen.envs[0]["DOCKER_HOST"] == "ssh://scan@scanvm"
    cli_env = popen.envs[1]
    assert "DOCKER_HOST" not in cli_env and not any("TOKEN" in k for k in cli_env)
    assert "run --detach" in docker.verbs()
    assert docker.verbs()[-2:] == ["rm -f", "rmi -f"]
    assert _work_dirs(tmp_path) == []
    assert ex.scan_log.slot_free()


def test_non_mcp_tools_at_startup_abort(tmp_path: Path) -> None:
    docker = FakeDocker()
    popen = FakePopen([{**INIT, "tools": [*INIT["tools"], "Bash"]}, RESULT], [FINDING])
    row = _run(_executor(tmp_path, popen, docker))
    assert row["state"] == "aborted" and "launch refused" in row["error"]
    assert docker.verbs()[-2:] == ["rm -f", "rmi -f"] and _work_dirs(tmp_path) == []
    assert row["findings_new"] == 0


def test_non_mcp_tool_call_mid_run_aborts(tmp_path: Path) -> None:
    docker = FakeDocker()
    popen = FakePopen([INIT, _tool("mcp__scan_sandbox__sandbox_read"), _tool("Bash"), RESULT], [])
    row = _run(_executor(tmp_path, popen, docker))
    assert row["state"] == "aborted" and "'Bash'" in row["error"]
    assert popen.procs[-1].killed.is_set()


def test_queued_ci_aborts_a_running_agent(tmp_path: Path) -> None:
    docker = FakeDocker()
    popen = FakePopen([INIT], [], hang=True)
    calls = {"n": 0}

    def queued() -> int:
        calls["n"] += 1
        return 0 if calls["n"] <= 2 else 4  # idle at admission + recheck, then CI arrives

    row = _run(_executor(tmp_path, popen, docker, queued_jobs=queued))
    assert row["state"] == "aborted" and "yielding to CI" in row["error"]
    assert popen.procs[-1].killed.is_set()
    assert docker.verbs()[-2:] == ["rm -f", "rmi -f"]


def test_turn_budget_aborts(tmp_path: Path) -> None:
    docker = FakeDocker()
    turns = [_tool("mcp__scan_sandbox__sandbox_list")] * 4
    popen = FakePopen([INIT, *turns, RESULT], [])
    row = _run(_executor(tmp_path, popen, docker, config=_config(executor={"cli_version": "2.1.263", "max_turns": 3})))
    assert row["state"] == "aborted" and "turn budget" in row["error"]


def test_error_result_fails_and_still_cleans_up(tmp_path: Path) -> None:
    docker = FakeDocker()
    popen = FakePopen([INIT, {**RESULT, "is_error": True, "result": "auth expired"}], [])
    row = _run(_executor(tmp_path, popen, docker))
    assert row["state"] == "failed" and "auth expired" in row["error"]
    assert docker.verbs()[-2:] == ["rm -f", "rmi -f"]


def test_cancel_before_build(tmp_path: Path) -> None:
    docker, popen = FakeDocker(), FakePopen([INIT, RESULT], [])
    ex = _executor(tmp_path, popen, docker)
    req = ScanRequest(repo="Tools", sha=SHA, dry_run=False)
    row = ex.submit(req)
    ex.cancelled.set()
    done = ex.execute(row["id"], req)
    assert done["state"] == "aborted" and "cancelled" in done["error"]
    assert popen.argvs == []


def test_slot_is_single(tmp_path: Path) -> None:
    docker, popen = FakeDocker(), FakePopen([], [])
    ex = _executor(tmp_path, popen, docker)
    first = ex.submit(ScanRequest(repo="Tools", sha=SHA, dry_run=False))
    second = ex.submit(ScanRequest(repo="Tools", sha=SHA, dry_run=False))
    assert first["state"] == "running" and second["state"] == "refused"
    assert any("slot" in r for r in second["reasons"])


def _tarball(members: dict[str, bytes], symlink: tuple[str, str] | None = None) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink[0])
            info.type = tarfile.SYMTYPE
            info.linkname = symlink[1]
            tar.addfile(info)
    buf.seek(0)
    return buf


def test_extract_tarball_strips_top_dir_and_drops_links(tmp_path: Path) -> None:
    buf = _tarball(
        {"Tools-abc/src/a.py": b"print(1)", "Tools-abc/.oss-scanner/Dockerfile": b"FROM x"},
        symlink=("Tools-abc/creds", "/home/runner/.ssh/id_ed25519"),
    )
    assert extract_tarball(buf, tmp_path) == 2
    assert (tmp_path / "src" / "a.py").read_bytes() == b"print(1)"
    assert not (tmp_path / "creds").exists()


def test_extract_tarball_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(tarfile.TarError):
        extract_tarball(_tarball({"Tools-abc/../../escape.txt": b"x"}), tmp_path / "ctx")


def test_extract_tarball_caps_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "MAX_SOURCE_FILES", 1)
    with pytest.raises(ScanAborted):
        extract_tarball(_tarball({"T/a": b"1", "T/b": b"2"}), tmp_path)
