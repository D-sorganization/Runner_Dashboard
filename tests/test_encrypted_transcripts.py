"""Contract tests for PM-8 Encrypted SENSITIVE transcripts (issue #3259, epic #3249)."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import encrypted_transcripts as et  # noqa: E402
import key_wrapping as kw  # noqa: E402
import private_mode as pm  # noqa: E402

CANARY_0 = b"Example Corp confidential design review chunk 0"
CANARY_1 = b"Example Corp confidential financials chunk 1"
CANARY_2 = b"Example Corp confidential roadmap chunk 2"
_ENABLED = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)


def _make_store(
    tmp_path: Path, *, private_mode: pm.PrivateModeConfig = _ENABLED, key_bytes: bytes | None = None
) -> et.EncryptedTranscriptStore:
    key_dir = tmp_path / "keys"
    key_dir.mkdir(exist_ok=True)
    key_path = key_dir / "vault.key"
    key_path.write_bytes(os.urandom(32) if key_bytes is None else key_bytes)
    os.chmod(key_path, 0o600)
    provider = kw.KeyWrappingProvider(
        backend="vault", private_mode=private_mode, env={"RUNNER_DASHBOARD_VAULT_KEY_PATH": str(key_path)}
    )
    return et.EncryptedTranscriptStore(
        store_dir=tmp_path / "transcripts", key_provider=provider, private_mode=private_mode
    )


def test_append_without_rewrite_and_no_plaintext_on_disk(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_thread("example-corp-1", authorized_principals=("operator:example-corp",), ttl_seconds=3600, now=100.0)
    assert store.append_chunk("example-corp-1", CANARY_0, now=101.0) == 0
    chunks_path = tmp_path / "transcripts" / "example-corp-1.chunks.jsonl"
    first_bytes = chunks_path.read_bytes()
    assert store.append_chunk("example-corp-1", CANARY_1, now=102.0) == 1
    second_bytes = chunks_path.read_bytes()
    assert len(second_bytes) > len(first_bytes) and second_bytes.startswith(first_bytes)

    for p in (tmp_path / "transcripts").rglob("*"):
        if p.is_file():
            raw = p.read_bytes()
            assert CANARY_0 not in raw and CANARY_1 not in raw and not p.name.endswith(".tmp")
    assert store.read_chunks("example-corp-1", principal="operator:example-corp", now=105.0) == (CANARY_0, CANARY_1)


def test_tamper_or_reorder_is_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_thread("example-corp-2", authorized_principals=("operator:example-corp",), ttl_seconds=3600, now=100.0)
    for idx, chunk in enumerate((CANARY_0, CANARY_1, CANARY_2), start=1):
        store.append_chunk("example-corp-2", chunk, now=100.0 + idx)
    chunks_path = tmp_path / "transcripts" / "example-corp-2.chunks.jsonl"
    lines = chunks_path.read_text(encoding="utf-8").splitlines()

    rec1 = {**json.loads(lines[1]), "seq": 0}
    rec0 = json.loads(lines[0])
    ct = bytearray(bytes.fromhex(rec0["ciphertext"]))
    ct[-1] ^= 0xFF
    rec0["ciphertext"] = bytes(ct).hex()

    for bad_payload in (
        "\n".join([lines[1], lines[0], lines[2]]) + "\n",
        "\n".join([lines[1], lines[2]]) + "\n",
        json.dumps(rec1) + "\n",
        "\n".join([json.dumps(rec0), lines[1], lines[2]]) + "\n",
    ):
        chunks_path.write_text(bad_payload, encoding="utf-8")
        with pytest.raises(et.TranscriptIntegrityError):
            store.read_chunks("example-corp-2", principal="operator:example-corp", now=105.0)


def test_authz_enforced_on_every_read(tmp_path: Path) -> None:
    denied: list[tuple[str, str]] = []

    def custom_authorizer(thread_id: str, principal: str, allowed: frozenset[str]) -> bool:
        ok = principal in allowed and not principal.endswith(":revoked")
        if not ok:
            denied.append((thread_id, principal))
        return ok

    base = _make_store(tmp_path)
    store = et.EncryptedTranscriptStore(
        store_dir=tmp_path / "transcripts",
        key_provider=base.key_provider,
        private_mode=_ENABLED,
        authorizer=custom_authorizer,
    )
    store.create_thread(
        "example-corp-3",
        authorized_principals=("operator:example-corp", "auditor:example-corp:revoked"),
        ttl_seconds=3600,
        now=100.0,
    )
    store.append_chunk("example-corp-3", CANARY_0, now=101.0)
    for bad_principal in ("intruder:example-corp", "auditor:example-corp:revoked"):
        with pytest.raises(et.TranscriptAuthzError):
            store.read_chunks("example-corp-3", principal=bad_principal, now=102.0)
    with pytest.raises(ValueError):
        store.read_chunks("example-corp-3", principal="   ", now=102.0)
    assert len(denied) == 2
    assert store.read_chunks("example-corp-3", principal="operator:example-corp", now=102.0) == (CANARY_0,)


def test_crypto_shred_makes_reads_fail_and_restore_cannot_resurrect(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_thread("example-corp-4", authorized_principals=("operator:example-corp",), ttl_seconds=3600, now=100.0)
    store.append_chunk("example-corp-4", CANARY_0, now=101.0)

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    meta_file = tmp_path / "transcripts" / "example-corp-4.meta.json"
    chunks_file = tmp_path / "transcripts" / "example-corp-4.chunks.jsonl"
    shutil.copy2(meta_file, backup_dir / meta_file.name)
    shutil.copy2(chunks_file, backup_dir / chunks_file.name)

    store.crypto_shred("example-corp-4", reason="user-requested-shred", now=110.0)
    assert not meta_file.exists() and not chunks_file.exists()
    with pytest.raises(et.TranscriptShreddedError):
        store.read_chunks("example-corp-4", principal="operator:example-corp", now=111.0)

    shutil.copy2(backup_dir / meta_file.name, meta_file)
    shutil.copy2(backup_dir / chunks_file.name, chunks_file)
    with pytest.raises(et.TranscriptShreddedError):
        store.read_chunks("example-corp-4", principal="operator:example-corp", now=112.0)
    with pytest.raises(et.TranscriptShreddedError):
        store.append_chunk("example-corp-4", CANARY_1, now=112.0)
    with pytest.raises(et.TranscriptShreddedError):
        store.create_thread("example-corp-4", authorized_principals=("operator:example-corp",), now=112.0)


def test_ttl_expiry_blocks_reads_and_purge_shreds(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_thread("example-corp-5", authorized_principals=("operator:example-corp",), ttl_seconds=60, now=100.0)
    store.append_chunk("example-corp-5", CANARY_0, now=110.0)
    assert store.read_chunks("example-corp-5", principal="operator:example-corp", now=159.0) == (CANARY_0,)
    with pytest.raises(et.TranscriptExpiredError):
        store.read_chunks("example-corp-5", principal="operator:example-corp", now=160.0)
    assert store.purge_expired(now=160.0) == ("example-corp-5",)
    with pytest.raises(et.TranscriptShreddedError):
        store.read_chunks("example-corp-5", principal="operator:example-corp", now=161.0)


def test_flag_off_never_decrypts_and_adr_documents_residue(tmp_path: Path) -> None:
    key_bytes = os.urandom(32)
    enabled_store = _make_store(tmp_path, private_mode=_ENABLED, key_bytes=key_bytes)
    enabled_store.create_thread("example-corp-6", authorized_principals=("operator:example-corp",), now=100.0)
    enabled_store.append_chunk("example-corp-6", CANARY_0, now=101.0)

    disabled_store = _make_store(tmp_path, private_mode=pm.DISABLED, key_bytes=key_bytes)
    assert disabled_store.is_enabled() is False
    with pytest.raises(et.TranscriptUnavailableError) as exc_info:
        disabled_store.read_chunks("example-corp-6", principal="operator:example-corp", now=102.0)
    assert exc_info.value.reason == "flag-disabled"

    adr_text = (_ROOT / "docs" / "adr" / "0009-private-mode-classification-levels.md").read_text(encoding="utf-8")
    for required_term in ("backups", "WAL", "indexes", "caches", "cannot resurrect a shredded thread"):
        assert required_term in adr_text
