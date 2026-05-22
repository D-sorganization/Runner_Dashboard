"""Tests for backend/quota_enforcement.py — issue #386."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import identity as id_mod
import pytest
import quota_enforcement as qe


def _make_principal(pid: str = "p1", max_runners: int = 2) -> id_mod.Principal:
    return id_mod.Principal(
        id=pid,
        type="bot",
        name="Bot",
        roles=["operator"],
        quotas=id_mod.Quota(max_runners=max_runners, agent_spend_usd_day=10.0),
    )


def _make_enforcer(tmp_path: Path) -> qe.QuotaEnforcement:
    with patch("quota_enforcement.validate_config_path"):
        return qe.QuotaEnforcement(config_dir=tmp_path)


# ---------------------------------------------------------------------------
# get_today_spend — new principal has zero spend
# ---------------------------------------------------------------------------


def test_get_today_spend_zero_for_new_principal(tmp_path: Path) -> None:
    qenf = _make_enforcer(tmp_path)
    assert qenf.get_today_spend("new-principal") == 0.0


# ---------------------------------------------------------------------------
# add_spend — accumulates
# ---------------------------------------------------------------------------


def test_add_spend_accumulates(tmp_path: Path) -> None:
    qenf = _make_enforcer(tmp_path)
    with patch("quota_enforcement.validate_config_path"):
        qenf.add_spend("p1", 1.5)
        qenf.add_spend("p1", 2.0)
    assert qenf.get_today_spend("p1") == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# check_dispatch_quota — combines runner and spend checks
# ---------------------------------------------------------------------------


def test_check_dispatch_quota_within_limit(tmp_path: Path) -> None:
    qenf = _make_enforcer(tmp_path)
    p = _make_principal(max_runners=3)
    with patch("quota_enforcement.lease_manager") as mock_lm:
        mock_lm.get_active_leases.return_value = []
        allowed, reason = qenf.check_dispatch_quota(p, estimated_cost=0.5)
    assert allowed is True
    assert reason is None


def test_check_dispatch_quota_runner_quota_exceeded(tmp_path: Path) -> None:
    qenf = _make_enforcer(tmp_path)
    p = _make_principal(max_runners=1)

    import runner_lease as rl

    fake_lease = rl.LeaseRecord(
        principal_id="p1",
        runner_id="runner-x",
        acquired_at=0.0,
        expires_at=99999999.0,
    )
    with patch("quota_enforcement.lease_manager") as mock_lm:
        mock_lm.get_active_leases.return_value = [fake_lease]
        allowed, reason = qenf.check_dispatch_quota(p)
    assert allowed is False
    assert reason is not None


def test_check_dispatch_quota_spend_exceeded(tmp_path: Path) -> None:
    qenf = _make_enforcer(tmp_path)
    p = _make_principal()
    # Simulate spend near the daily limit
    qenf.spend_records["p1"] = {__import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()): 9.5}
    with patch("quota_enforcement.lease_manager") as mock_lm:
        mock_lm.get_active_leases.return_value = []
        allowed, reason = qenf.check_dispatch_quota(p, estimated_cost=2.0)
    assert allowed is False
