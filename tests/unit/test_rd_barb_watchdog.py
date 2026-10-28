"""rd-barb watchdog: restart on the next provider after auth, rate-limit or crash (#3064)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from scripts import rd_barb_watchdog as wd  # noqa: E402

pytestmark = pytest.mark.unit

CHAIN = ["claude", "codex", "antigravity", "cursor-agent"]


class FakeNext:
    """Stands in for ``rd_barb_provider.py next``: walks CHAIN on every failure."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def __call__(self, current: str | None = None, failure: str | None = None, run_id: str | None = None) -> wd.Pick:
        self.calls.append((current, failure))
        if current is None:
            return wd.Pick("claude", "opus")
        idx = CHAIN.index(current) + 1
        return wd.Pick(CHAIN[idx], None) if idx < len(CHAIN) else wd.Pick(None, None)


def _launcher(results: list[tuple[int, str]]) -> tuple[list[tuple[str, str | None]], Any]:
    seen: list[tuple[str, str | None]] = []

    def launch(provider: str, model: str | None) -> wd.RunResult:
        seen.append((provider, model))
        code, out = results.pop(0)
        return wd.RunResult(code, out)

    return seen, launch


@pytest.mark.parametrize(
    ("provider", "code", "output", "expected"),
    [
        ("claude", 0, "done", None),
        ("claude", 1, "Error: token expired, please run claude auth login", "auth_expired"),
        ("codex", 1, "429 Too Many Requests: rate limit", "rate_limited"),
        ("claude", 1, "You have exceeded your maximum usage", "quota_exhausted"),
        ("claude", 1, "Traceback (most recent call last): boom", "crash"),
        ("cursor-agent", -9, "", "crash"),
    ],
)
def test_classify_maps_exit_and_output(provider: str, code: int, output: str, expected: str | None) -> None:
    assert wd.classify(provider, wd.RunResult(code, output)) == expected


def test_clean_exit_does_not_restart() -> None:
    nxt = FakeNext()
    seen, launch = _launcher([(0, "ok")])
    assert wd.supervise(launch=launch, next_provider=nxt, sleep=lambda _s: None) == 0
    assert seen == [("claude", "opus")]
    assert nxt.calls == [(None, None)]


def test_auth_expired_restarts_on_next_provider_via_next() -> None:
    nxt = FakeNext()
    seen, launch = _launcher([(1, "authentication expired"), (0, "ok")])
    assert wd.supervise(launch=launch, next_provider=nxt, sleep=lambda _s: None) == 0
    assert seen == [("claude", "opus"), ("codex", None)]
    assert nxt.calls == [(None, None), ("claude", "auth_expired")]


def test_walks_whole_chain_then_stops_when_exhausted() -> None:
    nxt = FakeNext()
    seen, launch = _launcher([(1, "rate limit 429")] * 4)
    assert wd.supervise(launch=launch, next_provider=nxt, sleep=lambda _s: None) == 1
    assert [p for p, _ in seen] == CHAIN
    assert nxt.calls[-1] == ("cursor-agent", "rate_limited")


def test_restart_cap_bounds_the_loop() -> None:
    def always_same(current: str | None = None, failure: str | None = None, run_id: str | None = None) -> wd.Pick:
        return wd.Pick("claude", None)

    seen, launch = _launcher([(1, "crash")] * 10)
    assert wd.supervise(launch=launch, next_provider=always_same, max_restarts=3, sleep=lambda _s: None) == 1
    assert len(seen) == 4


def test_backoff_grows_and_is_capped() -> None:
    sleeps: list[float] = []
    seen, launch = _launcher([(1, "boom")] * 4)
    wd.supervise(launch=launch, next_provider=FakeNext(), sleep=sleeps.append)
    assert sleeps == [30.0, 60.0, 90.0]
    assert wd.backoff_seconds(50) == wd.MAX_BACKOFF_S


def test_cli_next_parses_provider_json_and_passes_flags() -> None:
    calls: list[list[str]] = []

    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        out = json.dumps({"provider": "codex", "model": None, "switched": True, "previous": "claude"})
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")

    pick = wd.cli_next(current="claude", failure="auth_expired", run_id="r1", run=run)
    assert pick == wd.Pick("codex", None)
    assert calls[0][-7:] == ["next", "--current", "claude", "--failure", "auth_expired", "--run-id", "r1"]
    assert calls[0][1].endswith("scripts/rd_barb_provider.py")


def test_cli_next_exhausted_chain_returns_empty_pick() -> None:
    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        out = json.dumps({"provider": None, "model": None, "switched": True, "previous": "cursor-agent"})
        return subprocess.CompletedProcess(cmd, 1, stdout=out, stderr="")

    assert wd.cli_next(current="cursor-agent", failure="crash", run=run) == wd.Pick(None, None)


@pytest.mark.parametrize("stdout", ["", "not json", '{"model": "x"}'])
def test_cli_next_fails_closed_on_bad_output(stdout: str) -> None:
    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 2, stdout=stdout, stderr="trace")

    with pytest.raises(wd.WatchdogError):
        wd.cli_next(current="claude", failure="crash", run=run)


def test_launch_command_requires_config_and_fills_template() -> None:
    with pytest.raises(wd.WatchdogError):
        wd.launch_command({}, "claude", None)
    cmd = wd.launch_command(
        {"RD_BARB_LAUNCH_CMD": "bin/rd-barb-session --provider {provider} --model {model}"}, "codex", None
    )
    assert cmd == ["bin/rd-barb-session", "--provider", "codex", "--model", ""]


def test_main_fails_closed_without_launch_cmd(capsys: pytest.CaptureFixture[str]) -> None:
    assert wd.main(["run"], config={}, next_provider=FakeNext()) == 2
    assert "RD_BARB_LAUNCH_CMD" in capsys.readouterr().err


def test_main_dry_run_prints_command_and_launches_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = {"RD_BARB_LAUNCH_CMD": "bin/rd-barb-session {provider} {model}"}
    assert wd.main(["run", "--dry-run"], config=cfg, next_provider=FakeNext()) == 0
    assert capsys.readouterr().out.strip() == "bin/rd-barb-session claude opus"


def test_tail_keeps_last_bytes_only() -> None:
    assert wd.tail("x" * 10 + "END", 3) == "END"


@pytest.mark.skipif(not wd.PROVIDER_SCRIPT.exists(), reason="rd_barb_provider.py lands in #3063")
def test_real_next_without_failure_picks_default_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("RD_BARB_PROVIDER", "RD_BARB_MODEL", "RD_BARB_FAILOVER"):
        monkeypatch.delenv(key, raising=False)
    assert wd.cli_next() == wd.Pick("claude", "opus")


def test_load_config_env_launch_cmd_wins_and_ignores_unrelated_keys() -> None:
    cfg = wd._load_config({"RD_BARB_LAUNCH_CMD": "bin/s {provider}", "HOME": "/x", "RD_BARB_MODEL": ""})
    assert cfg["RD_BARB_LAUNCH_CMD"] == "bin/s {provider}"
    assert "HOME" not in cfg and "RD_BARB_MODEL" not in cfg


def test_restart_cap_sends_one_alert_with_recommended_action() -> None:
    sent: list[dict[str, Any]] = []

    def launch(_p: str, _m: str | None) -> wd.RunResult:
        return wd.RunResult(1, "segfault")

    def always_same(**_kw: Any) -> wd.Pick:
        return wd.Pick("claude", None)

    rc = wd.supervise(
        launch=launch, next_provider=always_same, max_restarts=2, sleep=lambda _s: None, run_id="r1", alert=sent.append
    )
    assert rc == 1 and len(sent) == 1
    msg = sent[0]
    assert msg["kind"] == "alert" and msg["urgency"] == "high" and msg["run_id"] == "r1"
    assert "claude" in msg["summary"] and "crash" in msg["summary"]
    assert msg["recommended_action"].strip() and "\n" not in msg["recommended_action"]
    assert msg["dedupe_key"] == "rd-barb-restart-cap-claude-crash"


def test_clean_exit_and_exhausted_chain_send_no_cap_alert() -> None:
    sent: list[dict[str, Any]] = []
    ok = wd.supervise(
        launch=lambda _p, _m: wd.RunResult(0, ""),
        next_provider=lambda **_kw: wd.Pick("claude", None),
        alert=sent.append,
    )
    assert ok == 0 and sent == []


def test_cap_alert_passes_the_notify_contract() -> None:
    rn = pytest.importorskip("scripts.rd_barb_notify")
    assert rn.validate(wd.cap_alert("codex", "rate_limited", 8, None)) == []


def test_alert_failure_never_breaks_supervise() -> None:
    def boom(_msg: dict[str, Any]) -> None:
        raise RuntimeError("webhook down")

    rc = wd.supervise(
        launch=lambda _p, _m: wd.RunResult(1, "x"),
        next_provider=lambda **_kw: wd.Pick("claude", None),
        max_restarts=0,
        alert=boom,
    )
    assert rc == 1
