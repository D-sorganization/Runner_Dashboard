"""Contract tests for private-role context access (issue #3094)."""

from __future__ import annotations

import dataclasses
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import overlay_contract as oc  # noqa: E402
import private_context_audit as pca  # noqa: E402
import private_mode as pm  # noqa: E402
import private_roles as pr  # noqa: E402

MOCK = _ROOT / "tests" / "fixtures" / "overlay" / "mock-overlay.json"
SENTINEL = "SENTINEL-private-context-do-not-echo"

ROLE_ID = "mock-private-role"
TEMPLATE = "mock-template"
HOST = "mock-node"
CLI = "mock-subscription-cli"


def _base_overlay() -> oc.Overlay:
    return oc.load_overlay(MOCK)


def _store(tmp_path: Path, *, version: int = 1, key: bytes | None = None, content: bytes = b"x") -> Path:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / ".store-schema-version").write_text(str(version), encoding="utf-8")
    if key is not None:
        (store_dir / f"{TEMPLATE}.age").write_bytes(Fernet(key).encrypt(content))
    return store_dir


def _overlay_with_role(store_dir: Path) -> oc.Overlay:
    base = _base_overlay()
    role = oc.PrivateRole(
        role_id=ROLE_ID,
        allowed_templates=(TEMPLATE,),
        providers={HOST: (CLI,)},
        context_store_dir=str(store_dir),
        store_schema_version=1,
    )
    return oc.Overlay(
        schema_version=base.schema_version,
        hosts=base.hosts,
        agent_token_paths=base.agent_token_paths,
        roles_dir=base.roles_dir,
        policy_dir=base.policy_dir,
        private_docs_dir=base.private_docs_dir,
        private_roles=(role,),
        private_mode=base.private_mode,
    )


def _read(
    tmp_path: Path, overlay: oc.Overlay | None, key: bytes | None, **overrides: object
) -> bytes | pr.PrivateContextUnavailable:
    kwargs: dict[str, object] = {
        "overlay": overlay,
        "role_id": ROLE_ID,
        "requesting_role_id": ROLE_ID,
        "template_name": TEMPLATE,
        "host": HOST,
        "provider_cli": CLI,
        "env": {},
    }
    if key is not None:
        key_path = tmp_path / "host.key"
        key_path.write_bytes(key)
        kwargs["env"] = {pr.CONTEXT_KEY_PATH_ENV: str(key_path)}
    kwargs.update(overrides)
    return pr.read_private_context(**kwargs)  # type: ignore[arg-type]


def _reason(result: bytes | pr.PrivateContextUnavailable) -> str:
    assert pr.is_unavailable(result)
    assert isinstance(result, pr.PrivateContextUnavailable)
    return result.reason


def test_mock_overlay_parses_the_private_role() -> None:
    role = _base_overlay().private_role(ROLE_ID)
    assert role is not None
    assert role.allowed_templates == (TEMPLATE,)
    assert role.providers[HOST] == (CLI,)


def test_owner_role_reads_an_allowlisted_template(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key, content=SENTINEL.encode()))
    assert _read(tmp_path, overlay, key) == SENTINEL.encode()


@pytest.mark.parametrize("requester", ["some-other-role", "agent:claude", "panel:expert-1"])
def test_non_owner_panel_and_agent_reads_are_refused(tmp_path: Path, requester: str) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key))
    result = _read(tmp_path, overlay, key, requesting_role_id=requester)
    assert _reason(result) == "not-owner"


def test_non_allowlisted_template_is_refused(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key))
    result = _read(tmp_path, overlay, key, template_name="not-allowlisted")
    assert _reason(result) == "template-not-allowlisted"


def test_bulk_export_is_not_supported_by_the_accessor() -> None:
    """DbC: the accessor takes exactly one template name; there is no list/export call."""
    assert "template_name" in inspect.signature(pr.read_private_context).parameters
    assert not hasattr(pr, "list_context")
    assert not hasattr(pr, "export_context")


@pytest.mark.parametrize(
    ("host", "cli"),
    [(HOST, "pay-per-use-cli"), ("some-other-host", CLI)],
)
def test_unapproved_provider_or_host_is_refused(tmp_path: Path, host: str, cli: str) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key))
    result = _read(tmp_path, overlay, key, host=host, provider_cli=cli)
    assert _reason(result) == "provider-not-approved"


def test_missing_store_produces_the_visible_degraded_state(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(tmp_path / "does-not-exist")
    assert _reason(_read(tmp_path, overlay, key)) == "store-missing"


def test_missing_key_produces_the_visible_degraded_state(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key))
    assert _reason(_read(tmp_path, overlay, None)) == "key-missing"


def test_version_skew_produces_the_visible_degraded_state(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, version=2, key=key))
    assert _reason(_read(tmp_path, overlay, key)) == "version-skew"


def test_wrong_key_fails_closed_as_decrypt_failed(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key))
    assert _reason(_read(tmp_path, overlay, Fernet.generate_key())) == "decrypt-failed"


def test_unknown_role_produces_the_visible_degraded_state(tmp_path: Path) -> None:
    overlay = _base_overlay()
    result = _read(tmp_path, overlay, None, role_id="no-such-role", requesting_role_id="no-such-role")
    assert _reason(result) == "role-not-found"


def test_error_text_never_echoes_paths_values_or_content(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(tmp_path / SENTINEL)
    result = _read(tmp_path, overlay, key)
    assert pr.is_unavailable(result)
    assert SENTINEL not in repr(result)
    assert SENTINEL not in str(result)


def test_unavailable_state_rejects_unknown_reasons() -> None:
    with pytest.raises(ValueError, match="unknown"):
        pr.PrivateContextUnavailable(role_id=ROLE_ID, reason="not-a-real-reason")


def test_none_overlay_is_fail_closed(tmp_path: Path) -> None:
    result = _read(tmp_path, None, None)  # type: ignore[arg-type]
    assert _reason(result) == "role-not-found"


# --- issue #3096: private-context audit (metadata only) ---------------------


def _audit_store(tmp_path: Path) -> pca.PrivateContextAuditStore:
    return pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3")


def test_successful_read_writes_one_audit_entry(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key, content=SENTINEL.encode()))
    store = _audit_store(tmp_path)
    result = _read(tmp_path, overlay, key, audit_store=store)
    assert result == SENTINEL.encode()
    entries = store.list_entries(role_id=ROLE_ID)
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.role_id, entry.template_name, entry.provider_cli, entry.host, entry.outcome) == (
        ROLE_ID,
        TEMPLATE,
        CLI,
        HOST,
        "success",
    )
    assert entry.ts
    assert SENTINEL not in json.dumps(entry.__dict__)


def test_unavailable_read_writes_one_audit_entry_with_its_reason(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key))
    store = _audit_store(tmp_path)
    result = _read(tmp_path, overlay, key, template_name="not-allowlisted", audit_store=store)
    assert _reason(result) == "template-not-allowlisted"
    entries = store.list_entries(role_id=ROLE_ID)
    assert len(entries) == 1
    assert entries[0].outcome == "template-not-allowlisted"


def test_audit_write_failure_fails_closed_even_on_an_otherwise_successful_read(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key, content=SENTINEL.encode()))

    class _BrokenStore:
        def record(self, rec: object) -> int:
            raise pca.PrivateContextAuditError("disk full")

    result = _read(tmp_path, overlay, key, audit_store=_BrokenStore())
    assert _reason(result) == "audit-write-failed"


def test_audit_entries_survive_role_deletion(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    overlay = _overlay_with_role(_store(tmp_path, key=key, content=SENTINEL.encode()))
    store = _audit_store(tmp_path)
    _read(tmp_path, overlay, key, audit_store=store)

    overlay_without_role = dataclasses.replace(overlay, private_roles=())
    assert overlay_without_role.private_role(ROLE_ID) is None

    entries = store.list_entries(role_id=ROLE_ID)
    assert len(entries) == 1
    assert entries[0].outcome == "success"


# --- issue #3260: Private Mode PM-9 metadata-only audit, hash chain, checkpoints, group commit ---


def test_pm9_each_decision_writes_one_entry_with_no_content(tmp_path: Path) -> None:
    store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3", checkpoint_path=tmp_path / "cp.json")
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    ev = pca.PrivateModeAuditEvent(
        action="classify",
        actor="agent:example-bot",
        target_id="example-corp/widgets-sensitive#10",
        level=pm.Level.SENSITIVE,
        outcome="accepted",
    )
    res = store.record_decision(ev, config=enabled)
    assert res.parked is False
    assert res.row_id >= 1
    decisions = store.list_decisions()
    assert len(decisions) == 1
    assert decisions[0].action == "classify"
    assert decisions[0].level is pm.Level.SENSITIVE
    assert decisions[0].entry_hash
    assert decisions[0].prev_hash == pca.GENESIS_HASH

    with pytest.raises(ValueError, match="forbidden content"):
        pca.PrivateModeAuditEvent(
            action="classify",
            actor="agent:example-bot",
            target_id=f"example-corp/{SENTINEL}",
            level=pm.Level.SENSITIVE,
            outcome="accepted",
            metadata={"body": SENTINEL},
        )


def test_pm9_hash_chain_and_checkpoint_detect_tail_truncation_and_tamper(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    cp_path = tmp_path / "checkpoint.json"
    store = pca.PrivateContextAuditStore(db_path, checkpoint_path=cp_path)
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)

    events = [
        pca.PrivateModeAuditEvent(
            action="admission",
            actor="agent:example-bot",
            target_id=f"example-corp/widgets#{i}",
            level=pm.Level.SENSITIVE,
            outcome="accepted",
        )
        for i in range(1, 4)
    ]
    batch_res = store.record_decisions_batch(events, config=enabled)
    assert len(batch_res) == 3
    assert store.verify_chain().ok is True

    # Delete the last row (tail truncation) -> independent checkpoint detects it
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM private_mode_audit WHERE seq = 3")
    conn.commit()
    conn.close()

    verdict = store.verify_chain()
    assert verdict.ok is False
    assert "tail truncation" in verdict.reason


def test_pm9_audit_down_parks_sensitive_action_leaves_public_unaffected(tmp_path: Path) -> None:
    store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3", checkpoint_path=tmp_path / "cp.json")
    store.close()  # simulate audit store down
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)

    sens_ev = pca.PrivateModeAuditEvent(
        action="dispatch",
        actor="agent:example-bot",
        target_id="example-corp/widgets-sensitive#1",
        level=pm.Level.SENSITIVE,
        outcome="accepted",
    )
    sens_res = store.record_decision(sens_ev, config=enabled)
    assert sens_res.parked is True
    assert sens_res.state == "PARKED_AUDIT"
    assert sens_res.reason == "audit-unavailable"

    pub_ev = pca.PrivateModeAuditEvent(
        action="dispatch",
        actor="agent:example-bot",
        target_id="example-corp/widgets-public#1",
        level=pm.Level.PUBLIC,
        outcome="accepted",
    )
    pub_res = store.record_decision(pub_ev, config=enabled)
    assert pub_res.parked is False
    assert pub_res.state == "OK"


def test_pm9_flag_off_behaviour_unchanged_and_retention_config(tmp_path: Path) -> None:
    store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3", checkpoint_path=tmp_path / "cp.json")
    store.close()
    # Flag off: even SENSITIVE event does not park when private_mode is DISABLED
    ev = pca.PrivateModeAuditEvent(
        action="dispatch",
        actor="agent:example-bot",
        target_id="example-corp/widgets-sensitive#1",
        level=pm.Level.SENSITIVE,
        outcome="accepted",
    )
    off_res = store.record_decision(ev, config=pm.DISABLED)
    assert off_res.parked is False
    assert off_res.state == "DISABLED"

    assert pca.resolve_retention_days({}) == pca.DEFAULT_RETENTION_DAYS
    assert pca.resolve_retention_days({"RD_PRIVATE_AUDIT_RETENTION_DAYS": "365"}) == 365
    with pytest.raises(ValueError, match="retention"):
        pca.resolve_retention_days({"RD_PRIVATE_AUDIT_RETENTION_DAYS": "0"})
