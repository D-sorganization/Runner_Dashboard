"""Contract tests for KeyWrappingProvider (PM-7, issue #3257, epic #3249).

Tests-first (TDD):
- wrap/unwrap round trip across both ``vault_store`` and ``#3094`` host-key backends
- wrong key or mismatched AAD rejected with visible degraded state (``unwrap-failed``)
- key missing / unsafe permissions -> visible degraded state (``key-missing`` / ``key-unsafe``)
- no key material, paths, or plaintext DEK values in errors or degraded-state strings
- Flag-off (``private_mode.DISABLED``) behaviour unchanged and refuses wrapping (``flag-disabled``)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import key_wrapping as kw  # noqa: E402
import overlay_contract as oc  # noqa: E402
import private_mode as pm  # noqa: E402

SAMPLE_OVERLAY = _ROOT / "tests" / "fixtures" / "overlay" / "sample-public-overlay.json"
CANARY_DEK = b"example-corp-dek-canary-32-bytes"
CANARY_KEK = b"example-corp-kek-canary-32-bytes"


def _enabled_config() -> pm.PrivateModeConfig:
    return pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)


def _vault_env(tmp_path: Path, key_bytes: bytes | None = None) -> dict[str, str]:
    key_path = tmp_path / "vault.key"
    if key_bytes is not None:
        key_path.write_bytes(key_bytes)
        os.chmod(key_path, 0o600)
    return {"RUNNER_DASHBOARD_VAULT_KEY_PATH": str(key_path)}


def _host_key_env(tmp_path: Path, key_bytes: bytes | None = None) -> dict[str, str]:
    key_path = tmp_path / "host-context.key"
    if key_bytes is not None:
        key_path.write_bytes(key_bytes)
        os.chmod(key_path, 0o600)
    return {kw.CONTEXT_KEY_PATH_ENV: str(key_path)}


def test_vault_backend_wrap_and_unwrap_round_trip(tmp_path: Path) -> None:
    env = _vault_env(tmp_path, os.urandom(32))
    provider = kw.KeyWrappingProvider(
        backend="vault",
        private_mode=_enabled_config(),
        env=env,
    )
    dek = kw.generate_dek()
    assert len(dek) == 32
    wrapped = provider.wrap_key(dek, context="thread:example-corp-1")
    assert isinstance(wrapped, kw.WrappedKey)
    assert wrapped.backend == "vault"
    assert wrapped.version == 1
    assert dek not in wrapped.ciphertext

    unwrapped = provider.unwrap_key(wrapped, context="thread:example-corp-1")
    assert unwrapped == dek


def test_host_key_backend_wrap_and_unwrap_round_trip_with_fernet_key(tmp_path: Path) -> None:
    fernet_key = Fernet.generate_key()
    env = _host_key_env(tmp_path, fernet_key)
    provider = kw.KeyWrappingProvider(
        backend="host_key",
        private_mode=_enabled_config(),
        env=env,
    )
    wrapped = provider.wrap_key(CANARY_DEK, context="thread:example-corp-2")
    assert isinstance(wrapped, kw.WrappedKey)
    assert wrapped.backend == "host_key"
    assert CANARY_DEK not in wrapped.ciphertext

    unwrapped = provider.unwrap_key(wrapped, context="thread:example-corp-2")
    assert unwrapped == CANARY_DEK


def test_wrong_key_is_rejected_on_vault_and_host_key(tmp_path: Path) -> None:
    (tmp_path / "k1").mkdir()
    env1 = _vault_env(tmp_path / "k1", os.urandom(32))
    (tmp_path / "k2").mkdir()
    env2 = _vault_env(tmp_path / "k2", os.urandom(32))
    p1 = kw.KeyWrappingProvider(backend="vault", private_mode=_enabled_config(), env=env1)
    p2 = kw.KeyWrappingProvider(backend="vault", private_mode=_enabled_config(), env=env2)
    wrapped = p1.wrap_key(CANARY_DEK, context="thread:example-corp-3")
    assert isinstance(wrapped, kw.WrappedKey)

    res = p2.unwrap_key(wrapped, context="thread:example-corp-3")
    assert kw.is_unavailable(res)
    assert isinstance(res, kw.KeyWrappingUnavailable)
    assert res.reason == "unwrap-failed"

    # Also wrong AAD context with same key must be rejected
    bad_ctx = p1.unwrap_key(wrapped, context="thread:wrong-context")
    assert kw.is_unavailable(bad_ctx)
    assert isinstance(bad_ctx, kw.KeyWrappingUnavailable)
    assert bad_ctx.reason == "unwrap-failed"

    # Host key backend with wrong Fernet key
    (tmp_path / "h1").mkdir()
    (tmp_path / "h2").mkdir()
    hp1 = kw.KeyWrappingProvider(
        backend="host_key",
        private_mode=_enabled_config(),
        env=_host_key_env(tmp_path / "h1", Fernet.generate_key()),
    )
    hp2 = kw.KeyWrappingProvider(
        backend="host_key",
        private_mode=_enabled_config(),
        env=_host_key_env(tmp_path / "h2", Fernet.generate_key()),
    )
    h_wrapped = hp1.wrap_key(CANARY_DEK, context="thread:example-corp-4")
    assert isinstance(h_wrapped, kw.WrappedKey)
    h_res = hp2.unwrap_key(h_wrapped, context="thread:example-corp-4")
    assert kw.is_unavailable(h_res)
    assert isinstance(h_res, kw.KeyWrappingUnavailable)
    assert h_res.reason == "unwrap-failed"


def test_missing_key_produces_visible_degraded_state_without_creating_files(tmp_path: Path) -> None:
    missing_vault_path = tmp_path / "nonexistent" / "vault.key"
    vp = kw.KeyWrappingProvider(
        backend="vault",
        private_mode=_enabled_config(),
        env={"RUNNER_DASHBOARD_VAULT_KEY_PATH": str(missing_vault_path)},
    )
    v_res = vp.wrap_key(CANARY_DEK, context="thread:example-corp-5")
    assert kw.is_unavailable(v_res)
    assert isinstance(v_res, kw.KeyWrappingUnavailable)
    assert v_res.reason == "key-missing"
    assert not missing_vault_path.exists()

    hp = kw.KeyWrappingProvider(
        backend="host_key",
        private_mode=_enabled_config(),
        env={},
    )
    h_res = hp.wrap_key(CANARY_DEK, context="thread:example-corp-5")
    assert kw.is_unavailable(h_res)
    assert isinstance(h_res, kw.KeyWrappingUnavailable)
    assert h_res.reason == "key-missing"


def test_invalid_key_material_produces_key_invalid_degraded_state(tmp_path: Path) -> None:
    bad_vault_env = _vault_env(tmp_path, b"short-key")
    vp = kw.KeyWrappingProvider(backend="vault", private_mode=_enabled_config(), env=bad_vault_env)
    v_res = vp.wrap_key(CANARY_DEK, context="thread:example-corp-6")
    assert kw.is_unavailable(v_res)
    assert isinstance(v_res, kw.KeyWrappingUnavailable)
    assert v_res.reason == "key-invalid"

    bad_host_env = _host_key_env(tmp_path, b"not-a-valid-fernet-or-32b-key")
    hp = kw.KeyWrappingProvider(backend="host_key", private_mode=_enabled_config(), env=bad_host_env)
    h_res = hp.wrap_key(CANARY_DEK, context="thread:example-corp-6")
    assert kw.is_unavailable(h_res)
    assert isinstance(h_res, kw.KeyWrappingUnavailable)
    assert h_res.reason == "key-invalid"


def test_no_key_material_paths_or_plaintext_in_errors_or_degraded_states(tmp_path: Path) -> None:
    secret_dir = tmp_path / "example-corp-secret-path"
    secret_dir.mkdir()
    env = _vault_env(secret_dir, CANARY_KEK)
    p1 = kw.KeyWrappingProvider(backend="vault", private_mode=_enabled_config(), env=env)
    wrapped = p1.wrap_key(CANARY_DEK, context="thread:example-corp-7")
    assert isinstance(wrapped, kw.WrappedKey)

    # Tamper ciphertext and check error representation
    tampered = kw.WrappedKey(
        version=wrapped.version,
        backend=wrapped.backend,
        nonce=wrapped.nonce,
        ciphertext=wrapped.ciphertext[:-1] + bytes([wrapped.ciphertext[-1] ^ 0xFF]),
    )
    res = p1.unwrap_key(tampered, context="thread:example-corp-7")
    assert kw.is_unavailable(res)
    text = f"{res!r} {res}"
    assert CANARY_DEK.decode() not in text
    assert CANARY_KEK.decode() not in text
    assert "example-corp-secret-path" not in text

    # Precondition ValueError also never echoes key material
    with pytest.raises(ValueError) as exc_info:
        p1.wrap_key(b"short-" + CANARY_DEK, context="thread:example-corp-7")
    assert CANARY_DEK.decode() not in str(exc_info.value)
    assert CANARY_KEK.decode() not in str(exc_info.value)


def test_flag_off_behaviour_is_unchanged_and_refuses_wrapping(tmp_path: Path) -> None:
    overlay = oc.load_overlay(SAMPLE_OVERLAY)
    assert overlay.private_mode == pm.DISABLED
    env = _vault_env(tmp_path, os.urandom(32))
    provider = kw.KeyWrappingProvider.from_overlay(overlay, backend="vault", env=env)
    assert provider.is_enabled() is False

    res = provider.wrap_key(CANARY_DEK, context="thread:example-corp-8")
    assert kw.is_unavailable(res)
    assert isinstance(res, kw.KeyWrappingUnavailable)
    assert res.reason == "flag-disabled"


def test_backend_mismatch_on_unwrap_is_rejected(tmp_path: Path) -> None:
    env = _vault_env(tmp_path, os.urandom(32))
    vp = kw.KeyWrappingProvider(backend="vault", private_mode=_enabled_config(), env=env)
    hp = kw.KeyWrappingProvider(backend="host_key", private_mode=_enabled_config(), env=env)
    wrapped = vp.wrap_key(CANARY_DEK, context="thread:example-corp-9")
    assert isinstance(wrapped, kw.WrappedKey)
    res = hp.unwrap_key(wrapped, context="thread:example-corp-9")
    assert kw.is_unavailable(res)
    assert isinstance(res, kw.KeyWrappingUnavailable)
    assert res.reason == "backend-mismatch"
