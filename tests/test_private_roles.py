"""Contract tests for private-role context access (issue #3094)."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import overlay_contract as oc  # noqa: E402
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
