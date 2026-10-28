"""KeyWrappingProvider (PM-7, issue #3257, epic #3249).

One key-management interface over the existing ``vault_store`` master key and
the ``#3094`` host-held private-context key (``RD_PRIVATE_CONTEXT_KEY_PATH``).
No third keystore is introduced (DRY).

Contract (DbC / LoD / DRY):

* Callers wrap and unwrap 32-byte Data Encryption Keys (DEKs) exclusively via
  :class:`KeyWrappingProvider` without opening key files, inspecting environment
  variables, or calling ``vault_store`` / ``private_roles`` key readers directly.
* Every wrap and unwrap binds an explicit non-empty ``context`` string (e.g.
  thread id or artifact id) into AES-256-GCM Additional Authenticated Data (AAD)
  along with the envelope version and backend name.
* When Private Mode is off (:data:`private_mode.DISABLED`), the provider is a
  safe no-op and returns ``KeyWrappingUnavailable(reason="flag-disabled")``
  without reading or creating any key file.
* Missing keys, unsafe permissions, malformed keys, backend mismatches, or
  authentication tag failures return a visible, non-sensitive
  :class:`KeyWrappingUnavailable` degraded state instead of raising past the
  caller or leaking key material, file paths, or plaintext DEKs.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from overlay_contract import Overlay, get_overlay
from platform_utils.vault_key_permissions import assert_master_key_file_permissions
from private_mode import DISABLED, PrivateModeConfig

CONTEXT_KEY_PATH_ENV = "RD_PRIVATE_CONTEXT_KEY_PATH"
DEK_LEN = 32
NONCE_LEN = 12
ENVELOPE_VERSION = 1

BackendName = str
BACKENDS: frozenset[BackendName] = frozenset({"vault", "host_key"})

Reason = str
_REASONS: frozenset[Reason] = frozenset(
    {
        "flag-disabled",
        "key-missing",
        "key-unsafe",
        "key-invalid",
        "backend-mismatch",
        "version-skew",
        "unwrap-failed",
    }
)


@dataclass(frozen=True)
class KeyWrappingUnavailable:
    """Visible, non-sensitive fail-closed degraded state for key wrapping."""

    backend: BackendName
    reason: Reason

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError("unknown KeyWrappingUnavailable backend")
        if self.reason not in _REASONS:
            raise ValueError(f"unknown KeyWrappingUnavailable reason: {self.reason}")


@dataclass(frozen=True)
class WrappedKey:
    """Authenticated wrapped DEK envelope."""

    version: int
    backend: BackendName
    nonce: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError("WrappedKey: unsupported backend")
        if len(self.nonce) != NONCE_LEN:
            raise ValueError(f"WrappedKey: nonce must be {NONCE_LEN} bytes")
        if not self.ciphertext:
            raise ValueError("WrappedKey: ciphertext must be non-empty")


def generate_dek() -> bytes:
    """Mint a fresh 32-byte Data Encryption Key (DEK)."""
    return secrets.token_bytes(DEK_LEN)


def is_unavailable(result: bytes | WrappedKey | KeyWrappingUnavailable) -> bool:
    """Return True if *result* is a degraded :class:`KeyWrappingUnavailable` state."""
    return isinstance(result, KeyWrappingUnavailable)


def _resolve_vault_key_path(env: Mapping[str, str]) -> Path:
    override = (env.get("RUNNER_DASHBOARD_VAULT_KEY_PATH") or env.get("DASHBOARD_VAULT_KEY_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "runner-dashboard" / "vault.key"


def _load_vault_kek(env: Mapping[str, str]) -> bytes | KeyWrappingUnavailable:
    path = _resolve_vault_key_path(env)
    if not path.is_file():
        return KeyWrappingUnavailable("vault", "key-missing")
    try:
        assert_master_key_file_permissions(path)
    except PermissionError:
        return KeyWrappingUnavailable("vault", "key-unsafe")
    except OSError:
        return KeyWrappingUnavailable("vault", "key-missing")
    try:
        raw = path.read_bytes()
    except OSError:
        return KeyWrappingUnavailable("vault", "key-missing")
    if len(raw) != DEK_LEN:
        return KeyWrappingUnavailable("vault", "key-invalid")
    return raw


def _load_host_kek(env: Mapping[str, str]) -> bytes | KeyWrappingUnavailable:
    raw_path = env.get(CONTEXT_KEY_PATH_ENV, "").strip()
    if not raw_path:
        return KeyWrappingUnavailable("host_key", "key-missing")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        return KeyWrappingUnavailable("host_key", "key-missing")
    try:
        assert_master_key_file_permissions(path)
    except PermissionError:
        return KeyWrappingUnavailable("host_key", "key-unsafe")
    except OSError:
        return KeyWrappingUnavailable("host_key", "key-missing")
    try:
        raw = path.read_bytes()
    except OSError:
        return KeyWrappingUnavailable("host_key", "key-missing")
    if len(raw) == DEK_LEN:
        return raw
    try:
        decoded = base64.urlsafe_b64decode(raw.strip())
    except (binascii.Error, ValueError):
        return KeyWrappingUnavailable("host_key", "key-invalid")
    if len(decoded) != DEK_LEN:
        return KeyWrappingUnavailable("host_key", "key-invalid")
    return decoded


def _aad(version: int, backend: BackendName, context: str) -> bytes:
    return f"pm7:v{version}:{backend}:{context}".encode()


class KeyWrappingProvider:
    """Single key-wrapping interface over ``vault_store`` and ``#3094`` host key."""

    def __init__(
        self,
        *,
        backend: BackendName = "vault",
        private_mode: PrivateModeConfig = DISABLED,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if backend not in BACKENDS:
            raise ValueError(f"KeyWrappingProvider: unsupported backend (expected one of {sorted(BACKENDS)})")
        self._backend = backend
        self._private_mode = private_mode
        self._env = os.environ if env is None else env

    @classmethod
    def from_overlay(
        cls,
        overlay: Overlay | None = None,
        *,
        backend: BackendName = "vault",
        env: Mapping[str, str] | None = None,
    ) -> KeyWrappingProvider:
        """Construct a provider whose flag state follows the validated overlay."""
        resolved_overlay = get_overlay(env) if overlay is None else overlay
        pm_config = resolved_overlay.private_mode if resolved_overlay is not None else DISABLED
        return cls(backend=backend, private_mode=pm_config, env=env)

    @property
    def backend(self) -> BackendName:
        return self._backend

    def is_enabled(self) -> bool:
        return self._private_mode.enabled

    def _load_kek(self) -> bytes | KeyWrappingUnavailable:
        if self._backend == "vault":
            return _load_vault_kek(self._env)
        return _load_host_kek(self._env)

    def wrap_key(self, dek: bytes, *, context: str) -> WrappedKey | KeyWrappingUnavailable:
        """Wrap a 32-byte *dek* bound to *context*, or return a degraded state."""
        if not isinstance(dek, (bytes, bytearray)) or len(dek) != DEK_LEN:
            raise ValueError(f"wrap_key: dek must be {DEK_LEN} bytes")
        if not isinstance(context, str) or not context.strip():
            raise ValueError("wrap_key: context must be a non-empty string")
        if not self._private_mode.enabled:
            return KeyWrappingUnavailable(self._backend, "flag-disabled")
        kek = self._load_kek()
        if isinstance(kek, KeyWrappingUnavailable):
            return kek
        nonce = secrets.token_bytes(NONCE_LEN)
        ciphertext = AESGCM(kek).encrypt(nonce, bytes(dek), _aad(ENVELOPE_VERSION, self._backend, context))
        return WrappedKey(
            version=ENVELOPE_VERSION,
            backend=self._backend,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def unwrap_key(self, wrapped: WrappedKey, *, context: str) -> bytes | KeyWrappingUnavailable:
        """Unwrap *wrapped* bound to *context*, or return a degraded state."""
        if not isinstance(wrapped, WrappedKey):
            raise ValueError("unwrap_key: wrapped must be a WrappedKey")
        if not isinstance(context, str) or not context.strip():
            raise ValueError("unwrap_key: context must be a non-empty string")
        if not self._private_mode.enabled:
            return KeyWrappingUnavailable(self._backend, "flag-disabled")
        if wrapped.backend != self._backend:
            return KeyWrappingUnavailable(self._backend, "backend-mismatch")
        if wrapped.version != ENVELOPE_VERSION:
            return KeyWrappingUnavailable(self._backend, "version-skew")
        kek = self._load_kek()
        if isinstance(kek, KeyWrappingUnavailable):
            return kek
        try:
            plaintext = AESGCM(kek).decrypt(
                wrapped.nonce,
                wrapped.ciphertext,
                _aad(wrapped.version, wrapped.backend, context),
            )
        except (InvalidTag, ValueError):
            return KeyWrappingUnavailable(self._backend, "unwrap-failed")
        if len(plaintext) != DEK_LEN:
            return KeyWrappingUnavailable(self._backend, "unwrap-failed")
        return plaintext
