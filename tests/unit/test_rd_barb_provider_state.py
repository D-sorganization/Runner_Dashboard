"""Tests for rd-barb active provider persistence and router readback (#3527, #3549)."""

from __future__ import annotations

import datetime as _dt_mod
import json
import logging
import sys
from pathlib import Path

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from staff import rd_barb_provider_state as ps  # noqa: E402
from staff.availability import resolve_provider_chain  # noqa: E402
from staff.provider_failover_config import FailoverStep, resolve_failover_chain  # noqa: E402
from staff.role_spec import parse_role  # noqa: E402

from scripts import rd_barb_provider as rp  # noqa: E402


def _rd_barb_role_data() -> dict[str, object]:
    return {
        "name": "rd-barb",
        "title": "RD Barb",
        "summary": "Persistent Barb seat.",
        "playbook": "docs/agents/rd-barb.md",
        "prompt_template": None,
        "instructions": "Monitor and shepherd.",
        "providers": ["claude", "codex", "antigravity", "cursor-agent"],
        "model": "claude-opus-5-5",
        "schedule": "*/30 * * * *",
        "window": None,
        "repos": ["Runner_Dashboard"],
        "scope": {},
        "budget": {"usd_per_run": 0, "usd_per_day": 0},
        "permissions": dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell", "notify_user"), False),
        "reports_to": "barb",
        "holds": [],
        "surface": "dashboard",
        "retry": {
            "max_attempts": 4,
            "failover_chain": [
                {"provider": "claude", "model": "claude-opus-5-5"},
                {"provider": "codex", "model": "gpt-6-luna"},
                {"provider": "antigravity"},
                {"provider": "cursor-agent"},
            ],
        },
    }


def test_record_and_read_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    ps.record_active_provider(
        "rd-barb", "codex", model="gpt-6-luna", failure_class="rate_limited", previous="claude", now=1000.0
    )
    snap = ps.read_active_provider("rd-barb", now=1010.0)
    assert snap is not None
    assert snap.provider == "codex"
    assert snap.model == "gpt-6-luna"
    assert snap.previous == "claude"
    assert ps.active_provider_payload("rd-barb", now=1010.0)["provider"] == "codex"


def test_router_reads_active_provider_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Router and provider chain resolvers read back fresh provider-active.json (#3549)."""
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    ps.record_active_provider(
        "rd-barb", "codex", model="gpt-6-luna", failure_class="quota_exhausted", previous="claude"
    )

    decision = rp.select({}, role="rd-barb")
    assert decision.provider == "codex"
    assert decision.model == "gpt-6-luna"
    assert decision.switched is False

    chat_chain = resolve_provider_chain("barb")
    assert chat_chain[0] == "codex"
    assert "claude" in chat_chain

    spec = parse_role(_rd_barb_role_data(), "rd-barb.yml")
    failover_steps = resolve_failover_chain(spec)
    assert failover_steps[0] == FailoverStep("codex", "gpt-6-luna")
    assert [s.provider for s in failover_steps] == ["codex", "claude", "antigravity", "cursor-agent"]


def test_stale_provider_active_falls_back_to_primary_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A stale provider-active.json logs a warning and falls back to the configured primary (#3549)."""
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    ps.record_active_provider(
        "rd-barb",
        "codex",
        model="gpt-6-luna",
        failure_class="rate_limited",
        previous="claude",
        now=1000.0,
    )

    with caplog.at_level(logging.WARNING, logger="dashboard.staff.rd_barb_provider_state"):
        snap = ps.read_active_provider("rd-barb", max_age_s=300.0, now=2000.0)
    assert snap is None
    assert any("stale" in rec.message.lower() for rec in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="dashboard.staff.rd_barb_provider_state"):
        decision = rp.select({}, role="rd-barb", now=1000.0 + ps.DEFAULT_MAX_AGE_S + 60.0)
    assert (decision.provider, decision.model) == ("claude", "opus")
    assert any("stale" in rec.message.lower() for rec in caplog.records)

    # Make the file stale relative to wall clock as well for chain resolvers
    ps.record_active_provider("rd-barb", "codex", model="gpt-6-luna", now=1.0)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="dashboard.staff.rd_barb_provider_state"):
        chat_chain = resolve_provider_chain("barb")
        spec = parse_role(_rd_barb_role_data(), "rd-barb.yml")
        failover_steps = resolve_failover_chain(spec)
    assert chat_chain[0] == "claude"
    assert failover_steps[0].provider == "claude"
    assert any("stale" in rec.message.lower() for rec in caplog.records)


@pytest.mark.parametrize(
    "corrupt_content",
    [
        "{not-valid-json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"role": "rd-barb", "provider": "", "updated_at": 1000.0}),
        json.dumps({"role": "rd-barb", "provider": "codex", "updated_at": "not-a-timestamp"}),
        json.dumps({"role": "rd-barb", "provider": "codex", "updated_at": 0}),
        json.dumps({"role": "rd-barb", "provider": "unauthorized-provider", "updated_at": 9999999999.0}),
    ],
)
def test_corrupt_provider_active_falls_back_to_primary_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    corrupt_content: str,
) -> None:
    """A corrupt provider-active.json logs a warning and falls back to the configured primary (#3549)."""
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    path = ps.state_path("rd-barb")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(corrupt_content, encoding="utf-8")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="dashboard.staff.rd_barb_provider_state"):
        decision = rp.select({}, role="rd-barb", now=1005.0)
    assert (decision.provider, decision.model) == ("claude", "opus")
    assert caplog.records, f"Expected warning for corrupt content: {corrupt_content}"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="dashboard.staff.rd_barb_provider_state"):
        chat_chain = resolve_provider_chain("barb")
        spec = parse_role(_rd_barb_role_data(), "rd-barb.yml")
        failover_steps = resolve_failover_chain(spec)
    assert chat_chain[0] == "claude"
    assert failover_steps[0].provider == "claude"
    assert caplog.records, f"Expected warning from chain resolvers for corrupt content: {corrupt_content}"
