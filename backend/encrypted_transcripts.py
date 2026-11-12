"""Encrypted SENSITIVE transcript store (PM-8, issue #3259, epic #3249)."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from key_wrapping import NONCE_LEN, KeyWrappingProvider, KeyWrappingUnavailable, WrappedKey, generate_dek
from private_mode import DISABLED, Level, PrivateModeConfig

SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 86_400
MAX_TTL_SECONDS = 604_800
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOMBSTONE_FILE = "shred_tombstones.jsonl"
Authorizer = Callable[[str, str, frozenset[str]], bool]


class TranscriptError(RuntimeError):
    """Base error for encrypted transcript operations (never echoes plaintext or keys)."""


class TranscriptUnavailableError(TranscriptError):
    """Raised when Private Mode is off or key wrapping is unavailable."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"encrypted transcript store unavailable: {reason}")
        self.reason = reason


class TranscriptAuthzError(TranscriptError):
    """Raised when a read caller is not authorized for the target thread."""


class TranscriptIntegrityError(TranscriptError):
    """Raised when chunk ordering, sequence numbers, metadata, or AEAD tags fail verification."""


class TranscriptShreddedError(TranscriptError):
    """Raised when reading, appending, or recreating a crypto-shredded thread."""


class TranscriptExpiredError(TranscriptError):
    """Raised when accessing a thread whose TTL has elapsed."""


@dataclass(frozen=True)
class ThreadMetadata:
    """Validated per-thread envelope metadata (contains wrapped DEK only, never plaintext)."""

    schema_version: int
    thread_id: str
    level: Level
    created_at: float
    expires_at: float
    authorized_principals: frozenset[str]
    wrapped_dek: WrappedKey


def _validate_thread_id(thread_id: str) -> str:
    if not isinstance(thread_id, str) or not _THREAD_ID_RE.match(thread_id):
        raise ValueError("thread_id must be 1..128 safe characters ([A-Za-z0-9._-])")
    return thread_id


def _wrap_context(thread_id: str, expires_at: float) -> str:
    return f"pm8:thread:{thread_id}:exp:{expires_at:.6f}"


def _chunk_aad(thread_id: str, level: Level, seq: int) -> bytes:
    return f"pm8:v{SCHEMA_VERSION}:{thread_id}:{level.name}:seq:{seq}".encode()


class EncryptedTranscriptStore:
    """Accessor-only encrypted store for SENSITIVE thread transcripts (PM-8)."""

    def __init__(
        self,
        *,
        store_dir: Path,
        key_provider: KeyWrappingProvider,
        private_mode: PrivateModeConfig = DISABLED,
        authorizer: Authorizer | None = None,
        max_ttl_seconds: int = MAX_TTL_SECONDS,
    ) -> None:
        if not isinstance(store_dir, Path) or not isinstance(key_provider, KeyWrappingProvider):
            raise ValueError("store_dir and key_provider are required")
        if not isinstance(private_mode, PrivateModeConfig) or max_ttl_seconds <= 0:
            raise ValueError("valid private_mode and positive max_ttl_seconds are required")
        self._store_dir = store_dir
        self._key_provider = key_provider
        self._private_mode = private_mode
        self._authorizer = authorizer
        self._max_ttl_seconds = max_ttl_seconds

    @property
    def key_provider(self) -> KeyWrappingProvider:
        return self._key_provider

    def is_enabled(self) -> bool:
        return self._private_mode.enabled and self._key_provider.is_enabled()

    def _require_enabled(self) -> None:
        if not self.is_enabled():
            raise TranscriptUnavailableError("flag-disabled")

    def _meta_path(self, thread_id: str) -> Path:
        return self._store_dir / f"{thread_id}.meta.json"

    def _chunks_path(self, thread_id: str) -> Path:
        return self._store_dir / f"{thread_id}.chunks.jsonl"

    def _tombstone_path(self) -> Path:
        return self._store_dir / _TOMBSTONE_FILE

    def _is_shredded(self, thread_id: str) -> bool:
        tombstone = self._tombstone_path()
        if not tombstone.is_file():
            return False
        return any(
            line.strip() and json.loads(line).get("thread_id") == thread_id
            for line in tombstone.read_text(encoding="utf-8").splitlines()
        )

    def _assert_not_shredded(self, thread_id: str) -> None:
        if self._is_shredded(thread_id):
            raise TranscriptShreddedError(f"thread {thread_id} has been crypto-shredded")

    def _load_metadata(self, thread_id: str) -> ThreadMetadata:
        self._assert_not_shredded(thread_id)
        meta_path = self._meta_path(thread_id)
        if not meta_path.is_file():
            raise TranscriptIntegrityError(f"missing metadata for thread {thread_id}")
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            w = raw["wrapped_dek"]
            wrapped = WrappedKey(
                int(w["version"]), str(w["backend"]), bytes.fromhex(w["nonce"]), bytes.fromhex(w["ciphertext"])
            )
            level = Level[str(raw["level"])]
            principals = frozenset(str(p) for p in raw["authorized_principals"] if str(p).strip())
            if int(raw["schema_version"]) != SCHEMA_VERSION or raw["thread_id"] != thread_id or not principals:
                raise ValueError("invalid metadata fields")
            created, expires = float(raw["created_at"]), float(raw["expires_at"])
            return ThreadMetadata(SCHEMA_VERSION, thread_id, level, created, expires, principals, wrapped)
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise TranscriptIntegrityError("malformed thread metadata") from exc

    def _unwrap_thread_dek(self, meta: ThreadMetadata) -> bytes:
        unwrapped = self._key_provider.unwrap_key(
            meta.wrapped_dek, context=_wrap_context(meta.thread_id, meta.expires_at)
        )
        if isinstance(unwrapped, KeyWrappingUnavailable):
            raise TranscriptUnavailableError(unwrapped.reason)
        return unwrapped

    def create_thread(
        self,
        thread_id: str,
        *,
        level: Level = Level.SENSITIVE,
        authorized_principals: Sequence[str],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: float | None = None,
    ) -> ThreadMetadata:
        """Mint a per-thread DEK, wrap it via PM-7, and initialize an empty chunk stream."""
        _validate_thread_id(thread_id)
        if not isinstance(level, Level) or level != Level.SENSITIVE:
            raise ValueError("create_thread: level must be Level.SENSITIVE")
        cleaned = tuple(p.strip() for p in authorized_principals if isinstance(p, str) and p.strip())
        if not cleaned or not isinstance(ttl_seconds, int) or not (0 < ttl_seconds <= self._max_ttl_seconds):
            raise ValueError("create_thread: valid authorized_principals and ttl_seconds are required")
        self._require_enabled()
        self._store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._assert_not_shredded(thread_id)
        meta_path, chunks_path = self._meta_path(thread_id), self._chunks_path(thread_id)
        if meta_path.exists() or chunks_path.exists():
            raise ValueError(f"create_thread: thread {thread_id} already exists")

        created_at = time.time() if now is None else float(now)
        expires_at = created_at + float(ttl_seconds)
        wrapped = self._key_provider.wrap_key(generate_dek(), context=_wrap_context(thread_id, expires_at))
        if isinstance(wrapped, KeyWrappingUnavailable):
            raise TranscriptUnavailableError(wrapped.reason)

        payload = {
            "schema_version": SCHEMA_VERSION,
            "thread_id": thread_id,
            "level": level.name,
            "created_at": created_at,
            "expires_at": expires_at,
            "authorized_principals": sorted(set(cleaned)),
            "wrapped_dek": {
                "version": wrapped.version,
                "backend": wrapped.backend,
                "nonce": wrapped.nonce.hex(),
                "ciphertext": wrapped.ciphertext.hex(),
            },
        }
        meta_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(meta_path, 0o600)
        chunks_path.write_bytes(b"")
        os.chmod(chunks_path, 0o600)
        return self._load_metadata(thread_id)

    def append_chunk(self, thread_id: str, plaintext: bytes, *, now: float | None = None) -> int:
        """Encrypt and append a single chunk with sequence-bound AAD without rewriting prior chunks."""
        _validate_thread_id(thread_id)
        if not isinstance(plaintext, (bytes, bytearray)) or len(plaintext) == 0:
            raise ValueError("append_chunk: plaintext must be non-empty bytes")
        self._require_enabled()
        meta = self._load_metadata(thread_id)
        if (time.time() if now is None else float(now)) >= meta.expires_at:
            raise TranscriptExpiredError(f"thread {thread_id} expired")

        chunks_path = self._chunks_path(thread_id)
        if not chunks_path.is_file():
            raise TranscriptIntegrityError(f"missing chunk stream for thread {thread_id}")
        seq = sum(1 for ln in chunks_path.read_text(encoding="utf-8").splitlines() if ln.strip())
        dek = self._unwrap_thread_dek(meta)
        nonce = secrets.token_bytes(NONCE_LEN)
        ct = AESGCM(dek).encrypt(nonce, bytes(plaintext), _chunk_aad(thread_id, meta.level, seq))
        with chunks_path.open("ab") as fh:
            fh.write((json.dumps({"seq": seq, "nonce": nonce.hex(), "ciphertext": ct.hex()}) + "\n").encode("utf-8"))
        return seq

    def read_chunks(self, thread_id: str, *, principal: str, now: float | None = None) -> tuple[bytes, ...]:
        """Authorize *principal*, verify TTL and sequence integrity, and decrypt chunks in order."""
        _validate_thread_id(thread_id)
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("read_chunks: principal must be a non-empty string")
        caller = principal.strip()
        self._require_enabled()
        meta = self._load_metadata(thread_id)
        allowed = (
            self._authorizer(thread_id, caller, meta.authorized_principals)
            if self._authorizer is not None
            else (caller in meta.authorized_principals)
        )
        if not allowed:
            raise TranscriptAuthzError(f"principal not authorized for thread {thread_id}")
        if (time.time() if now is None else float(now)) >= meta.expires_at:
            raise TranscriptExpiredError(f"thread {thread_id} expired")

        dek = self._unwrap_thread_dek(meta)
        chunks_path = self._chunks_path(thread_id)
        if not chunks_path.is_file():
            raise TranscriptIntegrityError(f"missing chunk stream for thread {thread_id}")

        aes = AESGCM(dek)
        out: list[bytes] = []
        for expected_seq, line in enumerate(chunks_path.read_text(encoding="utf-8").splitlines()):
            try:
                rec = json.loads(line)
                seq, nonce, ct = int(rec["seq"]), bytes.fromhex(rec["nonce"]), bytes.fromhex(rec["ciphertext"])
            except (KeyError, ValueError, TypeError) as exc:
                raise TranscriptIntegrityError("malformed chunk record") from exc
            if seq != expected_seq or len(nonce) != NONCE_LEN:
                raise TranscriptIntegrityError(f"chunk sequence mismatch at {expected_seq}")
            try:
                out.append(aes.decrypt(nonce, ct, _chunk_aad(thread_id, meta.level, expected_seq)))
            except (InvalidTag, ValueError) as exc:
                raise TranscriptIntegrityError(f"AEAD tag verification failed at seq {expected_seq}") from exc
        return tuple(out)

    def crypto_shred(self, thread_id: str, *, reason: str = "shredded", now: float | None = None) -> None:
        """Record a persistent tombstone and destroy the wrapped DEK and encrypted chunk stream."""
        _validate_thread_id(thread_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("crypto_shred: reason must be a non-empty string")
        self._store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        ts = time.time() if now is None else float(now)
        tombstone = self._tombstone_path()
        if not self._is_shredded(thread_id):
            entry = json.dumps({"thread_id": thread_id, "shredded_at": ts, "reason": reason.strip()}) + "\n"
            with tombstone.open("ab") as fh:
                fh.write(entry.encode("utf-8"))
            os.chmod(tombstone, 0o600)
        for path in (self._meta_path(thread_id), self._chunks_path(thread_id)):
            if path.is_file():
                if (size := path.stat().st_size) > 0:
                    path.write_bytes(b"\x00" * size)
                path.unlink()

    def purge_expired(self, *, now: float | None = None) -> tuple[str, ...]:
        """Crypto-shred all threads whose TTL has elapsed as of *now*."""
        self._require_enabled()
        if not self._store_dir.is_dir():
            return ()
        ts = time.time() if now is None else float(now)
        purged: list[str] = []
        for meta_file in sorted(self._store_dir.glob("*.meta.json")):
            thread_id = meta_file.name[: -len(".meta.json")]
            if not self._is_shredded(thread_id) and ts >= self._load_metadata(thread_id).expires_at:
                self.crypto_shred(thread_id, reason="ttl-expired", now=ts)
                purged.append(thread_id)
        return tuple(purged)
