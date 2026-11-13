"""Private Mode PM-16 private CI controller and status publisher (issue #3267, epic #3249)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from key_wrapping import NONCE_LEN, KeyWrappingProvider, KeyWrappingUnavailable, WrappedKey, generate_dek
from private_mode import DISABLED, ClassificationError, Level, PrivateModeConfig
from private_mode_emit import emit_shared

from .private_gate_manifest import load_manifest
from .private_sandbox_executor import (
    SANDBOX_LABEL,
    TOOLCHAIN_BIN,
    PrivateSandboxExecutor,
    SandboxJobSpec,
    is_private_ci_enabled,
)

TARGET_KINDS: frozenset[str] = frozenset({"pr_head", "merge_queue"})
VALID_STATES: frozenset[str] = frozenset({"pending", "running", "success", "failure", "error"})
DEFAULT_ARTIFACT_TTL_SECONDS: float = 86400.0
_MIN_SIGNING_KEY_LEN = 16
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,48}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FORBIDDEN_PUBLISHER_CRED_KEYS = frozenset(
    {"GITHUB_TOKEN", "GH_TOKEN", "GIT_ASKPASS", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY"}
)


@dataclass(frozen=True)
class PrivateCIAttestation:
    run_id: str
    repo: str
    sha: str
    target_kind: str
    manifest_digest: str
    environment_digest: str
    state: str
    signature: str


@dataclass(frozen=True)
class EncryptedArtifactRecord:
    run_id: str
    path: Path
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class ControllerRunResult:
    enabled: bool
    ran: bool
    state: str
    attestation: PrivateCIAttestation | None = None
    published_payload: Mapping[str, str] | None = None
    artifact: EncryptedArtifactRecord | None = None


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def compute_manifest_digest(gates: Sequence[Mapping[str, Any]] | None = None) -> str:
    return _sha256_json(list(gates) if gates is not None else load_manifest())


def compute_environment_digest(
    image_digest: str, *, toolchain_bin: str = TOOLCHAIN_BIN, sandbox_label: str = SANDBOX_LABEL
) -> str:
    if not isinstance(image_digest, str) or not image_digest.strip():
        raise ClassificationError("compute_environment_digest: image_digest must be a non-empty str")
    return _sha256_json(
        {"image_digest": image_digest.strip(), "sandbox_label": sandbox_label, "toolchain_bin": toolchain_bin}
    )


def _validate_signing_key(signing_key: bytes) -> bytes:
    if not isinstance(signing_key, (bytes, bytearray)) or len(signing_key) < _MIN_SIGNING_KEY_LEN:
        raise ClassificationError(f"signing_key must be bytes of length >= {_MIN_SIGNING_KEY_LEN}")
    return bytes(signing_key)


def _sign_fields(key: bytes, parts: Sequence[str]) -> str:
    return hmac.new(key, json.dumps(list(parts), separators=(",", ":")).encode("utf-8"), hashlib.sha256).hexdigest()


def create_attestation(
    run_id: str,
    repo: str,
    sha: str,
    target_kind: str,
    manifest_digest: str,
    environment_digest: str,
    state: str,
    signing_key: bytes,
) -> PrivateCIAttestation:
    if not (isinstance(run_id, str) and _RUN_ID_RE.match(run_id)):
        raise ClassificationError(f"create_attestation: invalid run_id {run_id!r}")
    if not (isinstance(repo, str) and _REPO_RE.match(repo) and isinstance(sha, str) and _SHA_RE.match(sha)):
        raise ClassificationError("create_attestation: invalid repo or sha")
    if target_kind not in TARGET_KINDS or state not in VALID_STATES:
        raise ClassificationError("create_attestation: invalid target_kind or state")
    if not all(isinstance(d, str) and len(d) == 64 for d in (manifest_digest, environment_digest)):
        raise ClassificationError("create_attestation: digests must be 64-char hex strings")
    key = _validate_signing_key(signing_key)
    sig = _sign_fields(key, (run_id, repo, sha, target_kind, manifest_digest, environment_digest, state))
    return PrivateCIAttestation(run_id, repo, sha, target_kind, manifest_digest, environment_digest, state, sig)


def verify_attestation(
    attestation: PrivateCIAttestation,
    expected_repo: str,
    expected_sha: str,
    expected_target_kind: str,
    expected_manifest_digest: str,
    expected_environment_digest: str,
    signing_key: bytes,
) -> None:
    if not isinstance(attestation, PrivateCIAttestation):
        raise ClassificationError("verify_attestation: attestation must be a PrivateCIAttestation")
    key = _validate_signing_key(signing_key)
    actual = (
        attestation.repo,
        attestation.sha,
        attestation.target_kind,
        attestation.manifest_digest,
        attestation.environment_digest,
    )
    expected = (
        expected_repo,
        expected_sha,
        expected_target_kind,
        expected_manifest_digest,
        expected_environment_digest,
    )
    if actual != expected:
        raise ClassificationError("verify_attestation: attestation rejected (bound field mismatch)")
    if not hmac.compare_digest(
        attestation.signature, _sign_fields(key, (attestation.run_id, *actual, attestation.state))
    ):
        raise ClassificationError("verify_attestation: attestation rejected (forged signature)")


class PrivateCIArtifactStore:
    """Encrypted local artifact store backed by KeyWrappingProvider (PM-7) with TTL GC."""

    def __init__(self, artifact_dir: Path, key_provider: KeyWrappingProvider) -> None:
        self._dir, self._keys = Path(artifact_dir), key_provider

    def _path_for(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
            raise ClassificationError(f"artifact store: invalid run_id {run_id!r}")
        return self._dir / f"{run_id}.enc.json"

    def store_artifact(
        self,
        run_id: str,
        payload: Mapping[str, Any],
        ttl_seconds: float = DEFAULT_ARTIFACT_TTL_SECONDS,
        now: float | None = None,
    ) -> EncryptedArtifactRecord:
        if ttl_seconds <= 0:
            raise ClassificationError("store_artifact: ttl_seconds must be > 0")
        path, created = self._path_for(run_id), float(time.time() if now is None else now)
        expires, ctx, dek = created + float(ttl_seconds), f"pm16:artifact:{run_id}", generate_dek()
        wrapped = self._keys.wrap_key(dek, context=ctx)
        if isinstance(wrapped, KeyWrappingUnavailable):
            raise ClassificationError(f"store_artifact: key wrapping unavailable ({wrapped.reason})")
        nonce = secrets.token_bytes(NONCE_LEN)
        ct = AESGCM(dek).encrypt(
            nonce, json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8"), ctx.encode("utf-8")
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
        env = {
            "created_at": created,
            "data_ct": b64(ct),
            "data_nonce": b64(nonce),
            "expires_at": expires,
            "run_id": run_id,
            "wrapped_backend": wrapped.backend,
            "wrapped_ct": b64(wrapped.ciphertext),
            "wrapped_nonce": b64(wrapped.nonce),
            "wrapped_version": wrapped.version,
        }
        path.write_text(json.dumps(env, sort_keys=True), encoding="utf-8")
        return EncryptedArtifactRecord(run_id=run_id, path=path, created_at=created, expires_at=expires)

    def read_artifact(self, run_id: str, *, now: float | None = None) -> dict[str, Any]:
        path = self._path_for(run_id)
        if not path.is_file():
            raise ClassificationError("read_artifact: artifact not found")
        env = json.loads(path.read_text(encoding="utf-8"))
        if float(time.time() if now is None else now) >= float(env["expires_at"]):
            raise ClassificationError("read_artifact: artifact has expired")
        ctx = f"pm16:artifact:{run_id}"
        b64d = base64.b64decode
        wrapped = WrappedKey(
            int(env["wrapped_version"]),
            str(env["wrapped_backend"]),
            b64d(env["wrapped_nonce"]),
            b64d(env["wrapped_ct"]),
        )
        dek = self._keys.unwrap_key(wrapped, context=ctx)
        if isinstance(dek, KeyWrappingUnavailable):
            raise ClassificationError(f"read_artifact: key unwrap failed ({dek.reason})")
        try:
            pt = AESGCM(dek).decrypt(b64d(env["data_nonce"]), b64d(env["data_ct"]), ctx.encode("utf-8"))
        except (InvalidTag, ValueError) as exc:
            raise ClassificationError("read_artifact: artifact ciphertext authentication failed") from exc
        return dict(json.loads(pt.decode("utf-8")))

    def gc_expired(self, *, now: float | None = None) -> list[str]:
        if not self._dir.is_dir():
            return []
        check_now, removed = float(time.time() if now is None else now), []
        for path in sorted(self._dir.glob("*.enc.json")):
            try:
                env = json.loads(path.read_text(encoding="utf-8"))
                expired, run_id = check_now >= float(env["expires_at"]), str(env.get("run_id") or path.stem[:-4])
            except (OSError, ValueError, KeyError):
                expired, run_id = True, path.stem.removesuffix(".enc")
            if expired:
                path.unlink(missing_ok=True)
                removed.append(run_id)
        return removed


class PrivateCIStatusPublisher:
    """Status publisher enforcing SoD (no sandbox creds) and projecting via emit_shared."""

    def __init__(
        self,
        github_sink: Callable[[dict[str, str]], None],
        signing_key: bytes,
        private_mode: PrivateModeConfig = DISABLED,
        publisher_env: Mapping[str, str] | None = None,
    ) -> None:
        self._key, self._sink, self._pm = _validate_signing_key(signing_key), github_sink, private_mode
        if publisher_env and any(
            k.upper() in _FORBIDDEN_PUBLISHER_CRED_KEYS or "SANDBOX" in k.upper() for k in publisher_env
        ):
            raise ClassificationError("SoD violation: publisher must not hold sandbox credential")

    def publish(
        self,
        attestation: PrivateCIAttestation,
        expected_repo: str,
        expected_sha: str,
        expected_target_kind: str,
        expected_manifest_digest: str,
        expected_environment_digest: str,
    ) -> dict[str, str]:
        verify_attestation(
            attestation,
            expected_repo,
            expected_sha,
            expected_target_kind,
            expected_manifest_digest,
            expected_environment_digest,
            self._key,
        )
        proj = emit_shared(
            "github_check",
            {"id": attestation.run_id, "state": attestation.state},
            level=Level.SENSITIVE,
            config=self._pm,
            mac_key=self._key,
        )
        payload = {"id": proj.opaque_id, "state": attestation.state}
        self._sink(payload)
        return payload


class PrivateCIController:
    """Orchestrates private CI execution at PR head and merge-queue SHAs."""

    def __init__(
        self,
        executor: PrivateSandboxExecutor,
        publisher: PrivateCIStatusPublisher,
        artifact_store: PrivateCIArtifactStore,
        signing_key: bytes,
        gates: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        self._executor, self._publisher, self._artifacts = executor, publisher, artifact_store
        self._key, self._gates = _validate_signing_key(signing_key), list(gates) if gates is not None else None

    def run_target(
        self,
        run_id: str,
        repo: str,
        sha: str,
        target_kind: str,
        enabled: bool | None = None,
        ttl_seconds: float = DEFAULT_ARTIFACT_TTL_SECONDS,
        now: float | None = None,
    ) -> ControllerRunResult:
        if target_kind not in TARGET_KINDS:
            raise ClassificationError(f"run_target: unsupported target_kind {target_kind!r}")
        if not is_private_ci_enabled(enabled=enabled):
            return ControllerRunResult(enabled=False, ran=False, state="disabled")
        gates = self._gates if self._gates is not None else load_manifest()
        job_res = self._executor.run_job(SandboxJobSpec(job_id=run_id, repo=repo, sha=sha), gates=gates, enabled=True)
        state = "success" if job_res.exit_code == 0 else "failure"
        m_dig, e_dig = compute_manifest_digest(gates), compute_environment_digest(job_res.image_digest)
        att = create_attestation(run_id, repo, sha, target_kind, m_dig, e_dig, state, self._key)
        art = self._artifacts.store_artifact(
            run_id, {"exit_code": job_res.exit_code, "target_kind": target_kind}, ttl_seconds, now
        )
        pub = self._publisher.publish(att, repo, sha, target_kind, m_dig, e_dig)
        return ControllerRunResult(True, True, state, att, pub, art)
