"""Pilot private role: work context (issue #3097, private-roles slice 4).

Exercises the slice-1/2/3 accessor, provider gate, redaction gate and audit
store end to end for exactly one pilot role, using placeholder/fixture
content only (never real work content). This role is NOT wired into hub
dispatch (see docs/private-roles/dry-run-checklist-work-context.md); that
wiring only happens after a human-supervised dry run passes.

Callers only ever use ``private_roles.read_private_context`` (LoD); this
suite never opens the audit store or gate modules on the role's behalf
outside of asserting on their effects.
"""

from __future__ import annotations

import dataclasses
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
import private_roles as pr  # noqa: E402
from private_mode import Level  # noqa: E402
from staff.private_surface_gate import PLACEHOLDER, tag  # noqa: E402
from staff.redaction import redact_sensitive_content  # noqa: E402

FIXTURE_OVERLAY = _ROOT / "tests" / "fixtures" / "overlay" / "work-context-pilot-overlay.json"
FIXTURE_TEMPLATE = _ROOT / "tests" / "fixtures" / "private_roles" / "work_context_daily_brief.txt"

ROLE_ID = "work-context-pilot"
TEMPLATE = "work-context-daily-brief"
HOST = "dieter-workstation"
CLI = "claude-subscription-cli"


def _fixture_content() -> bytes:
    return FIXTURE_TEMPLATE.read_bytes()


def _overlay_for_store(store_dir: Path) -> oc.Overlay:
    """The role definition fixture validated against the slice-1 schema, with
    only ``context_store_dir`` overlaid to point at a test-local store (the
    real overlay would point at a host-local path Dieter controls)."""
    base = oc.load_overlay(FIXTURE_OVERLAY)
    role = base.private_role(ROLE_ID)
    assert role is not None
    moved = dataclasses.replace(role, context_store_dir=str(store_dir))
    return dataclasses.replace(base, private_roles=(moved,))


def _store(tmp_path: Path, *, key: bytes | None, version: int = 1) -> Path:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / ".store-schema-version").write_text(str(version), encoding="utf-8")
    if key is not None:
        (store_dir / f"{TEMPLATE}.age").write_bytes(Fernet(key).encrypt(_fixture_content()))
    return store_dir


def _key_env(tmp_path: Path, key: bytes) -> dict[str, str]:
    key_path = tmp_path / "host.key"
    key_path.write_bytes(key)
    return {pr.CONTEXT_KEY_PATH_ENV: str(key_path)}


def test_role_definition_validates_against_the_slice1_schema() -> None:
    """Role definition fixture validates with mock overlay (acceptance criterion 1)."""
    overlay = oc.load_overlay(FIXTURE_OVERLAY)
    role = overlay.private_role(ROLE_ID)
    assert role is not None
    assert role.allowed_templates == (TEMPLATE,)
    assert role.providers[HOST] == (CLI,)
    assert role.store_schema_version == 1


def test_end_to_end_template_read_is_audited_and_redaction_gated(tmp_path: Path) -> None:
    """Acceptance criterion 2: template read, audited, output to board redacted."""
    key = Fernet.generate_key()
    overlay = _overlay_for_store(_store(tmp_path, key=key))
    audit_store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3")

    result = pr.read_private_context(
        overlay=overlay,
        role_id=ROLE_ID,
        requesting_role_id=ROLE_ID,
        template_name=TEMPLATE,
        host=HOST,
        provider_cli=CLI,
        env=_key_env(tmp_path, key),
        audit_store=audit_store,
    )
    assert not pr.is_unavailable(result)
    assert isinstance(result, bytes)
    assert result == _fixture_content()

    entries = audit_store.list_entries(role_id=ROLE_ID)
    assert len(entries) == 1
    assert entries[0].outcome == "success"
    assert entries[0].template_name == TEMPLATE
    assert entries[0].provider_cli == CLI
    assert entries[0].host == HOST

    # If this content were ever surfaced to the board, it must go through the
    # deny-by-default gate, not bypass it: unmarked/private-classified content
    # is withheld, and only an explicit PUBLIC tag is shareable.
    sensitive_span = tag(result.decode("utf-8"), Level.SENSITIVE)
    assert redact_sensitive_content(sensitive_span) == PLACEHOLDER
    assert _fixture_content().decode("utf-8") not in redact_sensitive_content(sensitive_span)

    public_span = tag(result.decode("utf-8"), Level.PUBLIC)
    assert redact_sensitive_content(public_span) == result.decode("utf-8")


def test_degraded_state_when_store_is_absent(tmp_path: Path) -> None:
    """Dry-run checklist item: degraded state shown when store absent, not a crash."""
    key = Fernet.generate_key()
    overlay = _overlay_for_store(tmp_path / "no-such-store-dir")
    audit_store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3")

    result = pr.read_private_context(
        overlay=overlay,
        role_id=ROLE_ID,
        requesting_role_id=ROLE_ID,
        template_name=TEMPLATE,
        host=HOST,
        provider_cli=CLI,
        env=_key_env(tmp_path, key),
        audit_store=audit_store,
    )
    assert isinstance(result, pr.PrivateContextUnavailable)
    assert result.reason == "store-missing"
    assert str(FIXTURE_TEMPLATE) not in repr(result)

    entries = audit_store.list_entries(role_id=ROLE_ID)
    assert len(entries) == 1
    assert entries[0].outcome == "store-missing"


@pytest.mark.parametrize(
    ("host", "cli"),
    [(HOST, "pay-per-use-cli"), ("some-unapproved-host", CLI)],
)
def test_provider_gate_blocks_an_unauthorized_provider(tmp_path: Path, host: str, cli: str) -> None:
    """Dry-run checklist item: provider gate."""
    key = Fernet.generate_key()
    overlay = _overlay_for_store(_store(tmp_path, key=key))
    audit_store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3")

    result = pr.read_private_context(
        overlay=overlay,
        role_id=ROLE_ID,
        requesting_role_id=ROLE_ID,
        template_name=TEMPLATE,
        host=host,
        provider_cli=cli,
        env=_key_env(tmp_path, key),
        audit_store=audit_store,
    )
    assert isinstance(result, pr.PrivateContextUnavailable)
    assert result.reason == "provider-not-approved"


def test_redaction_gate_fires_on_unclassified_and_non_public_content(tmp_path: Path) -> None:
    """Dry-run checklist item: redaction gate (deny-by-default, no bypass)."""
    key = Fernet.generate_key()
    overlay = _overlay_for_store(_store(tmp_path, key=key))
    audit_store = pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3")

    result = pr.read_private_context(
        overlay=overlay,
        role_id=ROLE_ID,
        requesting_role_id=ROLE_ID,
        template_name=TEMPLATE,
        host=HOST,
        provider_cli=CLI,
        env=_key_env(tmp_path, key),
        audit_store=audit_store,
    )
    assert isinstance(result, bytes)
    text = result.decode("utf-8")

    for level in (Level.SENSITIVE, Level.INTERNAL):
        gated = redact_sensitive_content(tag(text, level))
        assert text not in gated
        assert gated == PLACEHOLDER


def test_role_is_not_registered_for_hub_dispatch() -> None:
    """Issue #3097: no hub dispatch of this role until the supervised dry run passes."""
    dispatch_modules = [
        _ROOT / "backend" / "staff" / "role_dispatch.py",
        _ROOT / "backend" / "staff" / "role_registry.py",
    ]
    for path in dispatch_modules:
        if not path.exists():
            continue
        assert ROLE_ID not in path.read_text(encoding="utf-8")
