"""Config-driven per-role staff dispatch rate limits (#4457, epic #4251)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff import dispatch_rate_policy as drp


def test_resolve_code_reviewer_gets_higher_hourly_allowance() -> None:
    cap, rate = drp.resolve_dispatch_bucket("code-reviewer")
    assert cap == pytest.approx(30.0)
    assert rate == pytest.approx(30.0 / 3600.0)


def test_unknown_role_uses_default_bucket() -> None:
    cap, rate = drp.resolve_dispatch_bucket("night-watch")
    assert cap == pytest.approx(10.0)
    assert rate == pytest.approx(10.0 / 3600.0)


def test_dispatch_action_key_is_per_role() -> None:
    assert drp.dispatch_action_for_role("code-reviewer") == "dispatches:code-reviewer"
    assert drp.dispatch_action_for_role("ad-hoc") == "dispatches:ad-hoc"


def test_config_override_from_repo_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "staff_dispatch_rate_limits.json"
    cfg.write_text(
        '{"version":1,"default_dispatches_per_hour":5,"roles":{"code-reviewer":{"dispatches_per_hour":40}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_DISPATCH_RATE_LIMITS_PATH", str(cfg))
    drp.reload_dispatch_rate_policy()
    cap, _ = drp.resolve_dispatch_bucket("code-reviewer")
    assert cap == pytest.approx(40.0)
    cap_default, _ = drp.resolve_dispatch_bucket("bob")
    assert cap_default == pytest.approx(5.0)