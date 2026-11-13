"""rd-barb provider selection + failover (docs/agents/rd-barb.md)."""

from __future__ import annotations

import datetime as _dt_mod
import sys
from pathlib import Path

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from scripts import rd_barb_provider as rp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_rd_barb_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))


def test_default_is_claude_opus() -> None:
    d = rp.select({})
    assert (d.provider, d.model, d.switched) == ("claude", "opus", False)


def test_dieter_can_switch_default_provider_and_model() -> None:
    d = rp.select({"RD_BARB_PROVIDER": "codex", "RD_BARB_MODEL": "gpt-6-luna"})
    assert (d.provider, d.model) == ("codex", "gpt-6-luna")


def test_unknown_provider_falls_back_to_chain_head() -> None:
    d = rp.select({"RD_BARB_PROVIDER": "pay-per-use-api"})
    assert d.provider == "claude"


@pytest.mark.parametrize("failure", sorted(rp.SWITCH_ON))
def test_switch_failures_move_to_next_provider(failure: str) -> None:
    d = rp.select({}, current="claude", failure_class=failure)
    assert (d.provider, d.switched, d.previous) == ("codex", True, "claude")


def test_chain_order_is_claude_codex_agy_cursor() -> None:
    order = [s.provider for s in rp.chain({})]
    assert order == ["claude", "codex", "antigravity", "cursor-agent"]
    d = rp.select({}, current="antigravity", failure_class="crash")
    assert d.provider == "cursor-agent"


@pytest.mark.parametrize("failure", [None, "stalled", "idle_timeout", "needs_input"])
def test_non_provider_failures_restart_same_provider(failure: str | None) -> None:
    d = rp.select({}, current="codex", failure_class=failure)
    assert (d.provider, d.switched) == ("codex", False)


def test_exhausted_chain_returns_no_provider() -> None:
    d = rp.select({}, current="cursor-agent", failure_class="auth_expired")
    assert d.provider is None and d.switched


def test_custom_chain_from_config() -> None:
    cfg = {"RD_BARB_FAILOVER": "codex, claude"}
    assert [s.provider for s in rp.chain(cfg)] == ["codex", "claude"]


def test_switch_notice_is_update_with_recommendation() -> None:
    d = rp.select({}, current="claude", failure_class="auth_expired")
    msg = rp.switch_notice(d, failure_class="auth_expired", run_id="r1")
    assert msg is not None
    assert msg["kind"] == "update" and "codex" in msg["summary"]
    assert "Recommendation:" in msg["body"] and "claude" in msg["body"]
    assert msg["dedupe_key"].startswith("rd-barb-provider-")


def test_exhausted_notice_is_alert_with_recommended_action() -> None:
    d = rp.select({}, current="cursor-agent", failure_class="rate_limited")
    msg = rp.switch_notice(d, failure_class="rate_limited")
    assert msg is not None
    assert msg["kind"] == "alert" and msg["urgency"] == "high"
    assert msg["recommended_action"].strip()


def test_no_notice_when_not_switched() -> None:
    assert rp.switch_notice(rp.select({}), failure_class=None) is None


def test_main_prints_decision_and_sends_notice(capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[dict] = []
    rc = rp.main(["next", "--current", "claude", "--failure", "rate_limited"], config={}, notify=sent.append)
    out = capsys.readouterr().out
    assert rc == 0 and '"provider": "codex"' in out
    assert sent and sent[0]["kind"] == "update"


def test_role_flag_reads_role_prefixed_env_and_stamps_switch_notice(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = {"RD_BOB_PROVIDER": "codex", "RD_BOB_MODEL": "gpt-6.1-sol", "RD_BOB_FAILOVER": "codex,claude,antigravity"}
    assert rp.select(cfg, role="bob") == rp.Decision("codex", "gpt-6.1-sol", False)
    sent: list[dict] = []
    rc = rp.main(
        ["--role", "bob", "next", "--current", "codex", "--failure", "rate_limited"], config=cfg, notify=sent.append
    )
    out = capsys.readouterr().out
    assert rc == 0 and '"provider": "claude"' in out
    assert sent[0]["sender_role"] == "bob" and sent[0]["dedupe_key"] == "bob-provider-codex-claude-rate_limited"
    assert sent[0]["summary"].startswith("bob switched provider codex -> claude")
