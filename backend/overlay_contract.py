"""Core/private-overlay contract (issue #3079, epic #3077).

The public core never names private hosts, people or paths. Operators supply
them in a private overlay: one JSON file pointed to by ``RD_OVERLAY_PATH``.
This module is the only place that reads that file (LoD/DRY): callers ask the
accessor for a validated :class:`Overlay` and never open overlay files
themselves.

Contract (DbC):

* Unset ``RD_OVERLAY_PATH`` and no requirement flag -> ``None``. Behaviour is
  unchanged until cutover (backward compatible).
* ``RD_OVERLAY_REQUIRED=1`` with no path -> :class:`OverlayError` (refuse to
  start). The flag turns on at public cutover.
* A path that is missing, unreadable, not JSON, the wrong schema version, or
  has missing/unknown/mistyped keys -> :class:`OverlayError`.

Error messages name keys and reasons only. They never echo overlay values or
the overlay path, because both may be private.

The optional ``private_mode`` key (issue #3250, epic #3249) is additive: an
overlay that omits it validates exactly as it did before #3250, and
``Overlay.private_mode`` resolves to ``private_mode.DISABLED``. See
``backend/private_mode.py`` for the ``Level`` enum and the field's shape.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from private_mode import PrivateModeConfig
from private_mode import parse_private_mode as _parse_private_mode

OVERLAY_PATH_ENV = "RD_OVERLAY_PATH"
OVERLAY_REQUIRED_ENV = "RD_OVERLAY_REQUIRED"
OVERLAY_SCHEMA_VERSION = 1

_TOP_LEVEL_KEYS: dict[str, type] = {
    "schema_version": int,
    "hosts": list,
    "agent_token_paths": dict,
    "roles_dir": str,
    "policy_dir": str,
    "private_docs_dir": str,
    "private_roles": list,
}
_HOST_KEYS: dict[str, type] = {"name": str, "address": str}
_PRIVATE_ROLE_KEYS = frozenset(
    {"role_id", "allowed_templates", "providers", "context_store_dir", "store_schema_version"}
)
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class OverlayError(RuntimeError):
    """The overlay is required but missing, or present but invalid."""


@dataclass(frozen=True)
class OverlayHost:
    name: str
    address: str


@dataclass(frozen=True)
class PrivateRole:
    """A private-role definition (issue #3094). Lives only in the overlay."""

    role_id: str
    allowed_templates: tuple[str, ...]
    providers: Mapping[str, tuple[str, ...]]
    context_store_dir: str
    store_schema_version: int


@dataclass(frozen=True)
class Overlay:
    """A validated private overlay. Construct only via :func:`parse_overlay`."""

    schema_version: int
    hosts: tuple[OverlayHost, ...]
    agent_token_paths: Mapping[str, str]
    roles_dir: str
    policy_dir: str
    private_docs_dir: str
    private_roles: tuple[PrivateRole, ...]
    private_mode: PrivateModeConfig

    def host_names(self) -> tuple[str, ...]:
        return tuple(h.name for h in self.hosts)

    def private_role(self, role_id: str) -> PrivateRole | None:
        return next((r for r in self.private_roles if r.role_id == role_id), None)


def _non_empty_str_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise OverlayError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def _parse_private_role(raw: object, index: int) -> PrivateRole:
    where = f"overlay private_roles[{index}]"
    if not isinstance(raw, dict):
        raise OverlayError(f"{where}: must be an object")
    missing = sorted(_PRIVATE_ROLE_KEYS - set(raw))
    unknown = sorted(set(raw) - _PRIVATE_ROLE_KEYS)
    if missing:
        raise OverlayError(f"{where}: missing required key(s): {', '.join(missing)}")
    if unknown:
        raise OverlayError(f"{where}: unknown key(s): {', '.join(unknown)}")
    role_id = raw["role_id"]
    if not isinstance(role_id, str) or not role_id:
        raise OverlayError(f"{where}: role_id must be a non-empty string")
    templates = _non_empty_str_list(raw["allowed_templates"], f"{where}: allowed_templates")
    providers_raw = raw["providers"]
    if not isinstance(providers_raw, dict):
        raise OverlayError(f"{where}: providers must be an object")
    providers = {host: _non_empty_str_list(clis, f"{where}: providers[{host}]") for host, clis in providers_raw.items()}
    store_dir = raw["context_store_dir"]
    if not isinstance(store_dir, str) or not store_dir:
        raise OverlayError(f"{where}: context_store_dir must be a non-empty string")
    version = raw["store_schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise OverlayError(f"{where}: store_schema_version must be an int")
    return PrivateRole(
        role_id=role_id,
        allowed_templates=templates,
        providers=providers,
        context_store_dir=store_dir,
        store_schema_version=version,
    )


def _parse_private_roles(data: object) -> tuple[PrivateRole, ...]:
    if not isinstance(data, list):
        raise OverlayError("overlay private_roles: must be a list")
    roles = tuple(_parse_private_role(raw, i) for i, raw in enumerate(data))
    ids = [r.role_id for r in roles]
    if len(set(ids)) != len(ids):
        raise OverlayError("overlay private_roles: duplicate role_id")
    return roles


def _check_keys(data: Mapping[str, Any], spec: Mapping[str, type], where: str) -> None:
    missing = sorted(set(spec) - set(data))
    unknown = sorted(set(data) - set(spec))
    if missing:
        raise OverlayError(f"overlay {where}: missing required key(s): {', '.join(missing)}")
    if unknown:
        raise OverlayError(f"overlay {where}: unknown key(s): {', '.join(unknown)}")
    for key, expected in spec.items():
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, expected):
            raise OverlayError(f"overlay {where}: key '{key}' must be of type {expected.__name__}")
        if expected is str and not value:
            raise OverlayError(f"overlay {where}: key '{key}' must be non-empty")


def parse_overlay(data: object) -> Overlay:
    """Validate decoded overlay JSON and return an immutable :class:`Overlay`."""
    if not isinstance(data, dict):
        raise OverlayError("overlay: top level must be a JSON object")
    version = data.get("schema_version")
    if version != OVERLAY_SCHEMA_VERSION or isinstance(version, bool):
        raise OverlayError(f"overlay: unsupported schema_version (expected {OVERLAY_SCHEMA_VERSION})")
    # `private_mode` (issue #3250) is optional and additive: strip it before the
    # required/unknown-key check so existing overlays validate unchanged.
    required_body = {k: v for k, v in data.items() if k != "private_mode"}
    _check_keys(required_body, _TOP_LEVEL_KEYS, "root")
    try:
        private_mode = _parse_private_mode(data.get("private_mode"))
    except ValueError as exc:
        raise OverlayError(f"overlay {exc}") from exc
    hosts: list[OverlayHost] = []
    for i, raw in enumerate(data["hosts"]):
        if not isinstance(raw, dict):
            raise OverlayError(f"overlay hosts[{i}]: must be an object")
        _check_keys(raw, _HOST_KEYS, f"hosts[{i}]")
        hosts.append(OverlayHost(name=raw["name"], address=raw["address"]))
    if len({h.name for h in hosts}) != len(hosts):
        raise OverlayError("overlay hosts: duplicate host name")
    tokens = data["agent_token_paths"]
    for agent, path in tokens.items():
        if not isinstance(path, str) or not path:
            raise OverlayError(f"overlay agent_token_paths: entry '{agent}' must be a non-empty string")
    return Overlay(
        schema_version=version,
        hosts=tuple(hosts),
        agent_token_paths=dict(tokens),
        roles_dir=data["roles_dir"],
        policy_dir=data["policy_dir"],
        private_docs_dir=data["private_docs_dir"],
        private_roles=_parse_private_roles(data["private_roles"]),
        private_mode=private_mode,
    )


def load_overlay(path: Path) -> Overlay:
    """Read and validate the overlay file at *path*."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise OverlayError(f"overlay: file named by {OVERLAY_PATH_ENV} does not exist") from None
    except OSError:
        raise OverlayError(f"overlay: file named by {OVERLAY_PATH_ENV} is not readable") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise OverlayError("overlay: file is not valid JSON") from None
    return parse_overlay(data)


def overlay_required(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get(OVERLAY_REQUIRED_ENV, "").strip().lower() in _TRUTHY


def get_overlay(env: Mapping[str, str] | None = None) -> Overlay | None:
    """The single accessor for private overlay data.

    Returns ``None`` when no overlay is configured and none is required.
    """
    env = os.environ if env is None else env
    raw_path = env.get(OVERLAY_PATH_ENV, "").strip()
    if not raw_path:
        if overlay_required(env):
            raise OverlayError(f"overlay: {OVERLAY_REQUIRED_ENV} is set but {OVERLAY_PATH_ENV} is not")
        return None
    return load_overlay(Path(raw_path).expanduser())


def assert_overlay_ready(env: Mapping[str, str] | None = None) -> None:
    """Startup gate: raise :class:`OverlayError` so the server refuses to start."""
    get_overlay(env)


def overlay_companion_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """Parent directory of the overlay file for companion private settings (e.g. host_settings.json)."""
    env = os.environ if env is None else env
    raw_path = env.get(OVERLAY_PATH_ENV, "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser().parent
