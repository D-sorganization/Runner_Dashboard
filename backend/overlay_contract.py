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
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
}
_HOST_KEYS: dict[str, type] = {"name": str, "address": str}
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class OverlayError(RuntimeError):
    """The overlay is required but missing, or present but invalid."""


@dataclass(frozen=True)
class OverlayHost:
    name: str
    address: str


@dataclass(frozen=True)
class Overlay:
    """A validated private overlay. Construct only via :func:`parse_overlay`."""

    schema_version: int
    hosts: tuple[OverlayHost, ...]
    agent_token_paths: Mapping[str, str]
    roles_dir: str
    policy_dir: str
    private_docs_dir: str

    def host_names(self) -> tuple[str, ...]:
        return tuple(h.name for h in self.hosts)


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
    _check_keys(data, _TOP_LEVEL_KEYS, "root")
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
