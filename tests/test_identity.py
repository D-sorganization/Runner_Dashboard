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


# ---------------------------------------------------------------------------
# Issue #939a: save_tokens must be atomic (tempfile + os.replace), like
# save_principals — a crash mid-write must never leave a truncated tokens.yml.
# ---------------------------------------------------------------------------


def test_save_tokens_writes_atomically_via_replace(tmp_path: Path, monkeypatch) -> None:
    import os as _os

    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    mgr.tokens = [id_mod.TokenRecord(token_hash="h1", principal_id="p1", created_at=1.0, expires_at=2.0, name="t1")]

    seen: dict[str, str] = {}
    real_replace = _os.replace

    def _spy_replace(src, dst):
        seen["src"] = str(src)
        seen["dst"] = str(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(id_mod.os, "replace", _spy_replace)
    mgr.save_tokens()

    # Persisted via a temp file then atomically renamed onto tokens.yml.
    assert seen["dst"].endswith("tokens.yml")
    assert ".tmp-tokens-" in seen["src"]
    # Reloading yields the token back.
    mgr2 = id_mod.IdentityManager(config_dir=tmp_path)
    assert any(t.token_hash == "h1" for t in mgr2.tokens)


def test_save_tokens_crash_leaves_old_file_intact(tmp_path: Path, monkeypatch) -> None:
    """A failure during the dump must not corrupt an existing tokens.yml."""
    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    mgr.tokens = [id_mod.TokenRecord(token_hash="orig", principal_id="p1", created_at=1.0, expires_at=2.0, name="t1")]
    mgr.save_tokens()
    original = mgr.tokens_path.read_text(encoding="utf-8")

    # Simulate a crash mid-write: os.replace raises after the temp file is written.
    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(id_mod.os, "replace", _boom)
    mgr.tokens = [id_mod.TokenRecord(token_hash="new", principal_id="p1", created_at=3.0, expires_at=4.0, name="t2")]
    import pytest

    with pytest.raises(OSError, match="disk full"):
        mgr.save_tokens()

    # The original file is untouched (atomic replace never happened) and no
    # stray temp files leaked into the config dir.
    assert mgr.tokens_path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".tmp-tokens-*"))


# ---------------------------------------------------------------------------
# Issue #944: identity dir is CWD-independent and override-able
# ---------------------------------------------------------------------------


def test_resolve_identity_dir_honors_explicit_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(tmp_path / "ident"))
    assert id_mod.resolve_identity_dir() == (tmp_path / "ident").resolve()


def test_resolve_identity_dir_defaults_to_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DASHBOARD_IDENTITY_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert id_mod.resolve_identity_dir() == (tmp_path / "runner-dashboard").resolve()


def test_resolve_identity_dir_is_independent_of_cwd(monkeypatch, tmp_path: Path) -> None:
    # The old Path("config") default created a fresh empty store under whatever
    # CWD the server was launched from, silently losing all principals/tokens.
    monkeypatch.delenv("DASHBOARD_IDENTITY_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    here = id_mod.resolve_identity_dir()

    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    assert id_mod.resolve_identity_dir() == here


def test_identity_store_persists_across_cwd_change(monkeypatch, tmp_path: Path) -> None:
    ident_dir = tmp_path / "ident"
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(ident_dir))

    mgr = id_mod.IdentityManager(config_dir=id_mod.resolve_identity_dir())
    mgr.principals["alice"] = id_mod.Principal(id="alice", type="human", name="Alice")
    mgr.save_principals()

    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    reloaded = id_mod.IdentityManager(config_dir=id_mod.resolve_identity_dir())
    assert "alice" in reloaded.principals
