"""Per-role/global host preference contract (#3101). Unconfigured roles -> grokbot-first."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from staff.store import _config_dir

HostPreference = Literal["grokbot-first", "rd-first", "both"]
HostId = Literal["grokbot", "rd"]

PREFERENCES = frozenset({"grokbot-first", "rd-first", "both"})
HOST_IDS = frozenset({"grokbot", "rd"})
DEFAULT_PREFERENCE: HostPreference = "grokbot-first"
SCHEMA_VERSION = 1
_log = logging.getLogger("dashboard.staff.host_settings")
_HOST_SETTINGS_FILE = "host_settings.json"
OVERLAY_PATH_ENV = "RD_OVERLAY_PATH"
HOST_SETTINGS_PATH_ENV = "RD_HOST_SETTINGS_PATH"
_persist_lock = threading.RLock()


@dataclass(frozen=True)
class HostToggle:
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: object, *, where: str) -> HostToggle:
        if not isinstance(data, dict) or set(data) - {"enabled"}:
            raise ValueError(f"{where}: invalid hosts entry")
        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"{where}: enabled must be a boolean")
        return cls(enabled=enabled)


@dataclass
class HostSettingsBlock:
    preference: HostPreference = DEFAULT_PREFERENCE
    hosts: dict[str, HostToggle] = field(default_factory=lambda: {"grokbot": HostToggle(True), "rd": HostToggle(True)})

    @classmethod
    def default(cls) -> HostSettingsBlock:
        return cls()

    @classmethod
    def from_dict(cls, data: object, *, where: str) -> HostSettingsBlock:
        if not isinstance(data, dict) or set(data) - {"preference", "hosts"}:
            raise ValueError(f"{where}: invalid block")
        pref = data.get("preference", DEFAULT_PREFERENCE)
        if pref not in PREFERENCES:
            raise ValueError(f"{where}: invalid preference")
        hosts_raw = data.get("hosts", {})
        if not isinstance(hosts_raw, dict) or set(hosts_raw) - HOST_IDS:
            raise ValueError(f"{where}: invalid hosts")
        hosts = {
            hid: HostToggle.from_dict(hosts_raw.get(hid, {"enabled": True}), where=f"{where}.hosts.{hid}")
            for hid in HOST_IDS
        }
        return cls(preference=pref, hosts=hosts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preference": self.preference,
            "hosts": {hid: {"enabled": self.hosts[hid].enabled} for hid in sorted(HOST_IDS)},
        }


@dataclass
class HostSettingsDocument:
    schema_version: int = SCHEMA_VERSION
    global_default: HostSettingsBlock = field(default_factory=HostSettingsBlock.default)
    roles: dict[str, HostSettingsBlock] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> HostSettingsDocument:
        return cls()

    @classmethod
    def from_dict(cls, data: object) -> HostSettingsDocument:
        if data is None:
            return cls.empty()
        if not isinstance(data, dict):
            raise ValueError("host settings: top level must be an object")
        version = data.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION or isinstance(version, bool):
            raise ValueError("host settings: unsupported schema_version")
        if set(data) - {"schema_version", "global", "roles"}:
            raise ValueError("host settings: unknown top-level key(s)")
        roles_raw = data.get("roles", {})
        if not isinstance(roles_raw, dict):
            raise ValueError("host settings: roles must be an object")
        roles = {
            str(role).strip(): HostSettingsBlock.from_dict(block, where=f"roles.{role}")
            for role, block in roles_raw.items()
            if isinstance(role, str) and role.strip()
        }
        if len(roles) != len(roles_raw):
            raise ValueError("host settings: role name must be a non-empty string")
        return cls(
            schema_version=version,
            global_default=HostSettingsBlock.from_dict(data.get("global", {}), where="global"),
            roles=roles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "global": self.global_default.to_dict(),
            "roles": {name: self.roles[name].to_dict() for name in sorted(self.roles)},
        }


@dataclass(frozen=True)
class ResolvedRoleHost:
    role: str
    preference: HostPreference
    leader: HostId | None
    followers: tuple[HostId, ...]
    hosts_enabled: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "preference": self.preference,
            "leader": self.leader,
            "followers": list(self.followers),
            "hosts_enabled": self.hosts_enabled,
        }


def resolve_role_leader(role: str, doc: HostSettingsDocument | None = None) -> ResolvedRoleHost:
    block = doc.roles.get(role) if doc else None
    block = block or (doc.global_default if doc else HostSettingsBlock.default())
    enabled = tuple(h for h in ("grokbot", "rd") if block.hosts[h].enabled)
    order = ("rd", "grokbot") if block.preference == "rd-first" else ("grokbot", "rd")
    leader: HostId | None = None
    followers: list[HostId] = []
    for hid in order:
        if hid not in enabled:
            continue
        if leader is None:
            leader = hid
        else:
            followers.append(hid)
    if block.preference == "both" and len(enabled) == 2 and leader:
        followers = [h for h in enabled if h != leader]
    return ResolvedRoleHost(
        role=role,
        preference=block.preference,
        leader=leader,
        followers=tuple(followers),
        hosts_enabled={hid: block.hosts[hid].enabled for hid in HOST_IDS},
    )


def host_settings_path() -> Path:
    override = os.environ.get(HOST_SETTINGS_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    overlay = os.environ.get(OVERLAY_PATH_ENV, "").strip()
    if overlay:
        return Path(overlay).expanduser().parent / _HOST_SETTINGS_FILE
    return _config_dir() / _HOST_SETTINGS_FILE


def load_host_settings(path: Path | None = None) -> HostSettingsDocument | None:
    p = path or host_settings_path()
    with _persist_lock:
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("host settings file unreadable (%s)", exc.__class__.__name__)
            raise ValueError("host settings file is not valid JSON") from exc
    return HostSettingsDocument.from_dict(raw)


def save_host_settings(doc: HostSettingsDocument, path: Path | None = None) -> HostSettingsDocument:
    p = path or host_settings_path()
    payload = doc.to_dict()
    HostSettingsDocument.from_dict(payload)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _persist_lock:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(p)
    return doc
