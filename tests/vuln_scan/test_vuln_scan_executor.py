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
from vuln_scan import source as source_mod
from vuln_scan import watch as watch_mod
from vuln_scan.agent_io import apply_verdicts
from vuln_scan.executor import Probes, ScanExecutor, ScanRequest
from vuln_scan.findings import FindingsStore, validate_finding
from vuln_scan.operator_config import parse_operator_config
from vuln_scan.sandbox import ExecResult
from vuln_scan.scans import ScanLog
from vuln_scan.source import extract_tarball
from vuln_scan.watch import ScanAborted

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
VERIFY_INIT = {
    **INIT,
    "tools": [
        "mcp__scan_sandbox__report_verdict",
        "mcp__scan_sandbox__sandbox_exec",
        "mcp__scan_sandbox__sandbox_list",
        "mcp__scan_sandbox__sandbox_read",
    ],
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
    def __init__(self, volumes: str = "null", leftovers: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.volumes = volumes
        self.leftovers = leftovers

    def __call__(self, argv: Sequence[str], timeout: float, cap: int, env: Mapping[str, str]) -> ExecResult:
        self.calls.append(list(argv))
        if argv[1:3] == ["image", "inspect"]:
            return ExecResult(0, f"{DIGEST} {self.volumes}\n", "")
        if argv[1:3] in (["ps", "-aq"], ["image", "ls"]):
            return ExecResult(0, "".join(f"{i}\n" for i in self.leftovers), "")
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
        self.stdout = _Lines(self._stream())
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


class _Lines:
    """``readline(limit)`` over a generator of lines, like a text pipe."""

    def __init__(self, lines: Iterator[str]) -> None:
        self._lines = lines
        self._buf = ""

    def readline(self, limit: int = -1) -> str:
        if not self._buf:
            self._buf = next(self._lines, "")
        if 0 <= limit < len(self._buf):
            out, self._buf = self._buf[:limit], self._buf[limit:]
            return out
        out, self._buf = self._buf, ""
        return out


class FakePopen:
    """docker build prints progress; claude writes findings and streams events."""

    def __init__(
        self,
        events: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        hang: bool = False,
        verify_events: list[dict[str, Any]] | None = None,
        verdicts: tuple[str, ...] = ("reproduced",),
    ) -> None:
        self.events = events
        self.findings = findings
        self.hang = hang
        self.verify_events = verify_events
        self.verdicts = verdicts
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
            events, records = self.events, self.findings
            if args[args.index("--mode") + 1] == "verify":
                ids = sorted(args[args.index("--finding-ids") + 1].split(","))
                events = self.verify_events or [VERIFY_INIT, RESULT]
                verdicts = [*self.verdicts, *["not_reproduced"] * len(ids)]
                pairs = zip(ids, verdicts, strict=False)
                records = [{"finding_id": i, "verdict": v, "evidence": "ran it"} for i, v in pairs]
            out.write_text("".join(json.dumps(f) + "\n" for f in records), encoding="utf-8")
            proc = FakeProc([json.dumps(e) + "\n" for e in events], hang=self.hang)
        self.procs.append(proc)
        return proc


CONTRACT = """\
repo: https://github.com/D-sorganization/Tools
primary_contact: security@example.org
"""


def _source(contract: str | None = CONTRACT, dockerfile: bool = True) -> Any:
    def fetch(repo: str, sha: str, dest: Path) -> None:
        (dest / "README").write_text("x", encoding="utf-8")
        scanner = dest / ".oss-scanner"
        scanner.mkdir()
        if contract is not None:
            (scanner / "project.yaml").write_text(contract, encoding="utf-8")
        if dockerfile:
            (scanner / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
        (scanner / "threat_model.md").write_text("# Threats\n", encoding="utf-8")

    return fetch


def _probes(**overrides: Any) -> Probes:
    values: dict[str, Any] = {
        "default_branch_sha": lambda repo: SHA,
        "contract": lambda repo: {"valid": True, "dockerfile": ".oss-scanner/Dockerfile"},
        "queued_jobs": lambda: 0,
        "free_gb": lambda host: 200.0,
        "build_filter_ok": lambda host: True,
        "oauth_ok": lambda: True,
        "cli_version": lambda: "2.1.263 (Claude Code)",
        "fetch_source": _source(),
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
        org="D-sorganization",
    )


def _run(ex: ScanExecutor, dry_run: bool = False) -> dict[str, Any]:
    req = ScanRequest(repo="Tools", sha=SHA, dry_run=dry_run, requested_by="tester")
    row = ex.submit(req)
    if row["state"] != "running":
        return row
    return ex.execute(row["id"], req)


def _cleaned(docker: FakeDocker) -> bool:
    """Cleanup ran in order: containers (with volumes), the image, the builder cache."""
    return docker.verbs()[-3:] == ["rm -f", "rmi -f", "buildx prune"] and all(
        c[1:4] == ["rm", "-f", "-v"] for c in docker.calls if c[1:3] == ["rm", "-f"]
    )


def _work_dirs(tmp_path: Path) -> list[Path]:
    return [p for p in tmp_path.iterdir() if p.name.startswith("vs-")]


def test_dry_run_plans_without_docker_or_cli(tmp_path: Path) -> None:
    docker, popen = FakeDocker(), FakePopen([], [])
    row = _run(_executor(tmp_path, popen, docker), dry_run=True)
    assert row["state"] == "planned" and row["reasons"] == []
    assert docker.calls == [] and popen.argvs == []


def test_refused_without_a_vm_host(tmp_path: Path) -> None:
    docker, popen = FakeDocker(), FakePopen([], [])
    config = _config(scan_hosts={"node-c": {"isolation": "container"}})
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
    no_verify = {"cli_version": "2.1.263", "verifier_model": None}
    ex = _executor(tmp_path, popen, docker, config=_config(executor=no_verify))
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
    assert _cleaned(docker)
    assert _work_dirs(tmp_path) == []
    assert ex.scan_log.slot_free()


def test_non_mcp_tools_at_startup_abort(tmp_path: Path) -> None:
    docker = FakeDocker()
    popen = FakePopen([{**INIT, "tools": [*INIT["tools"], "Bash"]}, RESULT], [FINDING])
    row = _run(_executor(tmp_path, popen, docker))
    assert row["state"] == "aborted" and "launch refused" in row["error"]
    assert _cleaned(docker) and _work_dirs(tmp_path) == []
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
    assert _cleaned(docker)


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
    assert _cleaned(docker)


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
    monkeypatch.setattr(source_mod, "MAX_SOURCE_FILES", 1)
    with pytest.raises(ScanAborted):
        extract_tarball(_tarball({"T/a": b"1", "T/b": b"2"}), tmp_path)


# ── verifier (#2573) ────────────────────────────────────────────────────


def test_verifier_confirms_reproduced_and_marks_the_rest_inconclusive(tmp_path: Path) -> None:
    docker = FakeDocker()
    findings = [FINDING, {**FINDING, "symbol": "b"}]
    popen = FakePopen([INIT, RESULT], findings, verdicts=("reproduced", "not_reproduced"))
    ex = _executor(tmp_path, popen, docker)
    row = _run(ex)
    assert row["state"] == "succeeded", row["error"]
    assert row["cost_usd"] == 2.5  # scan + verify
    stored = ex.store.list(repo="Tools")
    assert sorted(f["state"] for f in stored) == ["confirmed", "inconclusive"]
    for f in stored:
        assert ex.store.audit(f["id"])[-1]["actor"] == "verifier:claude-sonnet-5-5"
    verify_argv = popen.argvs[-1]
    assert verify_argv[verify_argv.index("--model") + 1] == "claude-sonnet-5-5"
    allowed = verify_argv[verify_argv.index("--allowedTools") + 1]
    assert "report_verdict" in allowed and "report_finding" not in allowed
    runs = [c for c in docker.calls if c[1:3] == ["run", "--detach"]]
    names = [c[c.index("--name") + 1] for c in runs]
    assert len(names) == 2 and names[1] == names[0] + "-v"  # fresh container, same image
    removed = [c[4] for c in docker.calls if c[1:4] == ["rm", "-f", "-v"]]
    assert set(removed) == set(names) and docker.verbs()[-2:] == ["rmi -f", "buildx prune"]


def test_verifier_with_the_scan_tool_set_aborts_and_findings_stay_new(tmp_path: Path) -> None:
    docker = FakeDocker()
    popen = FakePopen([INIT, RESULT], [FINDING], verify_events=[INIT, RESULT])
    ex = _executor(tmp_path, popen, docker)
    row = _run(ex)
    assert row["state"] == "aborted" and "launch refused" in row["error"]
    assert [f["state"] for f in ex.store.list(repo="Tools")] == ["new"]
    assert _work_dirs(tmp_path) == []


def test_operator_triage_wins_over_a_late_verdict(tmp_path: Path) -> None:
    ex = _executor(tmp_path, FakePopen([], []), FakeDocker())
    fid, _ = ex.store.record("Tools", validate_finding(FINDING), scan_sha=SHA)
    ex.store.triage(fid, "false_positive", actor="op", note="test fixture")
    out = tmp_path / "verdicts.jsonl"
    out.write_text(json.dumps({"finding_id": fid, "verdict": "reproduced", "evidence": "x"}), encoding="utf-8")
    apply_verdicts(ex.store, out, {fid}, "claude-sonnet-5-5")
    assert ex.store.get(fid)["state"] == "false_positive"


def test_missing_verdict_is_inconclusive_never_false_positive(tmp_path: Path) -> None:
    ex = _executor(tmp_path, FakePopen([], []), FakeDocker())
    fid, _ = ex.store.record("Tools", validate_finding(FINDING), scan_sha=SHA)
    apply_verdicts(ex.store, tmp_path / "absent.jsonl", {fid}, "claude-sonnet-5-5")
    assert ex.store.get(fid)["state"] == "inconclusive"


def test_verifier_must_be_a_different_model() -> None:
    same = {"cli_version": "2.1.263", "model": "claude-opus-5-5", "verifier_model": "claude-opus-5-5"}
    config = _config(executor=same)
    assert config.executor.verifier_model is None
    assert any("must differ" in e for e in config.errors)


def test_plan_reports_reasons_without_writing_a_row(tmp_path: Path) -> None:
    ex = _executor(tmp_path, FakePopen([], []), FakeDocker(), now=lambda: datetime(2026, 10, 23, 20, 0, tzinfo=UTC))
    reasons = ex.plan(ScanRequest(repo="Tools", sha=SHA, dry_run=False))
    assert any("window" in r for r in reasons)
    assert ex.scan_log.recent() == []


# ── panel q5 hardening (#2603) ──────────────────────────────────────────


def test_digest_is_recorded_on_the_scan_row(tmp_path: Path) -> None:
    ex = _executor(tmp_path, FakePopen([INIT, RESULT], []), FakeDocker())
    row = _run(ex)
    assert row["state"] == "succeeded", row["error"]
    assert row["image_digest"] == DIGEST


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        (_source(contract=None), "no project.yaml"),
        (_source(contract=CONTRACT + "disabled: true\n"), "disabled: true"),
        (_source(dockerfile=False), "invalid"),
        (_source(contract="repo: https://github.com/someone-else/Tools\nprimary_contact: a@b.org\n"), "invalid"),
    ],
)
def test_contract_is_rechecked_at_the_scanned_sha(tmp_path: Path, source: Any, needle: str) -> None:
    docker, popen = FakeDocker(), FakePopen([INIT, RESULT], [])
    row = _run(_executor(tmp_path, popen, docker, fetch_source=source))
    assert row["state"] == "aborted" and needle in row["error"], row["error"]
    assert popen.argvs == []  # nothing was built
    assert _work_dirs(tmp_path) == []


def test_the_build_follows_the_dockerfile_of_the_scanned_sha(tmp_path: Path) -> None:
    custom = CONTRACT + "dockerfile: .oss-scanner/Dockerfile\n"
    popen = FakePopen([INIT, RESULT], [])
    _run(_executor(tmp_path, popen, FakeDocker(), fetch_source=_source(contract=custom)))
    build = popen.argvs[0]
    assert build[build.index("--file") + 1].replace("\\", "/").endswith("context/.oss-scanner/Dockerfile")


def test_an_image_declaring_a_volume_is_refused(tmp_path: Path) -> None:
    docker, popen = FakeDocker(volumes='{"/data":{}}'), FakePopen([INIT, RESULT], [])
    row = _run(_executor(tmp_path, popen, docker))
    assert row["state"] == "aborted" and "VOLUME" in row["error"]
    assert "run --detach" not in docker.verbs() and _cleaned(docker)


def test_leftovers_from_a_dead_process_are_swept_first(tmp_path: Path) -> None:
    docker = FakeDocker(leftovers=("0123456789ab", "not an id; rm -rf /"))
    _run(_executor(tmp_path, FakePopen([INIT, RESULT], []), docker))
    sweep = [c for c in docker.calls if c[:4] == ["docker", "rm", "-f", "-v"] and "0123456789ab" in c]
    assert sweep == [["docker", "rm", "-f", "-v", "0123456789ab"]]
    assert docker.calls[0][1:3] == ["ps", "-aq"]  # before any build


def test_an_oversized_agent_event_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch_mod, "MAX_LINE_CHARS", 1000)
    huge = json.dumps(_tool("mcp__scan_sandbox__sandbox_list")) + " " * 5000
    popen = FakePopen([INIT], [])
    original = popen.__call__

    def call(argv: list[str], **kwargs: Any) -> Any:
        proc = original(argv, **kwargs)
        if argv[0] != "docker":
            proc.stdout = _Lines(iter([json.dumps(INIT) + "\n", huge + "\n"]))
        return proc

    row = _run(_executor(tmp_path, call, FakeDocker()))  # type: ignore[arg-type]
    assert row["state"] == "aborted" and "over 1000 chars" in row["error"]


def test_long_build_output_lines_are_dropped_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch_mod, "MAX_LINE_CHARS", 1000)
    popen = FakePopen([INIT, RESULT], [])
    original = popen.__call__

    def call(argv: list[str], **kwargs: Any) -> Any:
        proc = original(argv, **kwargs)
        if argv[0] == "docker":
            proc.stdout = _Lines(iter(["#1 " + "x" * 5000 + "\n", "#9 DONE\n"]))
        return proc

    row = _run(_executor(tmp_path, call, FakeDocker()))  # type: ignore[arg-type]
    assert row["state"] == "succeeded", row["error"]
