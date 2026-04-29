"""Structural tests for the BiometricUnlock component."""

from pathlib import Path


def _read_frontend(path_from_repo: str) -> str:
    repo = Path(__file__).resolve().parents[2]
    return (repo / path_from_repo).read_text()


def test_biometric_unlock_component_exports_function() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "export function BiometricUnlock()" in src


def test_biometric_unlock_has_unlock_status_type() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert 'type UnlockStatus = "idle" | "prompting" | "success" | "error"' in src


def test_biometric_unlock_calls_register_begin() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "/api/auth/webauthn/register/begin" in src


def test_biometric_unlock_calls_assert_begin() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "/api/auth/webauthn/assert/begin" in src


def test_biometric_unlock_calls_credentials_list() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "/api/auth/webauthn/credentials" in src


def test_biometric_unlock_has_revoke_handler() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "revokeCredential" in src
    assert "DELETE" in src


def test_biometric_unlock_has_base64url_helpers() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "base64urlToBuffer" in src
    assert "bufferToBase64url" in src


def test_biometric_unlock_has_platform_authenticator_check() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "isUserVerifyingPlatformAuthenticatorAvailable" in src


def test_biometric_unlock_has_ui_states() -> None:
    src = _read_frontend("frontend/src/pages/BiometricUnlock.tsx")
    assert "Unlock with Biometrics" in src
    assert "Register Device" in src
    assert "Registered Credentials" in src
