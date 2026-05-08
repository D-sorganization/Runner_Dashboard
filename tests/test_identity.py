"""Tests for backend/identity.py — issue #386."""

from __future__ import annotations

from pathlib import Path

import identity as id_mod

# ---------------------------------------------------------------------------
# Principal model
# ---------------------------------------------------------------------------


def test_principal_minimal_fields() -> None:
    p = id_mod.Principal(id="p1", type="bot", name="TestBot")
    assert p.id == "p1"
    assert p.type == "bot"
    assert p.roles == []


def test_principal_with_roles() -> None:
    p = id_mod.Principal(id="p2", type="human", name="Alice", roles=["admin", "operator"])
    assert "admin" in p.roles


def test_principal_default_quota() -> None:
    p = id_mod.Principal(id="x", type="bot", name="Bot")
    assert p.quotas.max_runners >= 0
    assert p.quotas.agent_spend_usd_day >= 0.0


# ---------------------------------------------------------------------------
# TokenRecord model
# ---------------------------------------------------------------------------


def test_token_record_required_fields() -> None:
    rec = id_mod.TokenRecord(
        token_hash="abc123",
        principal_id="p1",
        created_at=1000.0,
        expires_at=2000.0,
        name="my-token",
    )
    assert rec.token_hash == "abc123"
    assert rec.expires_at == 2000.0


def test_token_record_no_expiry() -> None:
    rec = id_mod.TokenRecord(
        token_hash="xyz",
        principal_id="p1",
        created_at=1000.0,
        name="no-expire",
    )
    assert rec.expires_at is None


# ---------------------------------------------------------------------------
# IdentityManager — empty config dir
# ---------------------------------------------------------------------------


def test_identity_manager_creates_empty_principals(tmp_path: Path) -> None:
    # IdentityManager passes its own config_dir as the allowed_root, so
    # tmp_path is always within tmp_path — no patching needed.
    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    assert isinstance(mgr.principals, dict)


def test_identity_manager_get_nonexistent_principal(tmp_path: Path) -> None:
    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    result = mgr.get_principal("does-not-exist")
    assert result is None


def test_identity_manager_principals_dict_empty(tmp_path: Path) -> None:
    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    # principals dict should be empty for a fresh config dir
    assert isinstance(mgr.principals, dict)
    assert len(mgr.principals) == 0
