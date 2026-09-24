#!/usr/bin/env python3
"""Fleet machine registry loading and merge helpers.

The registry is stored as repo-managed YAML or JSON beside the dashboard
backend. It gives the dashboard and scheduled maintenance jobs a canonical
source of truth for machine identity, aliases, roles, and maintenance hints.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - deployment installs PyYAML
    yaml = None  # type: ignore[assignment]

from security import safe_yaml_load, validate_config_path

log = logging.getLogger("dashboard.machine_registry")
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("machine_registry.yml")


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        raise ValueError(f"Expected a string list, got {type(value).__name__}")

    result: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _coerce_number(value: Any, *, field: str) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Machine registry field '{field}' must be numeric")
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Machine registry field '{field}' must be numeric") from exc
    return int(number) if number.is_integer() else number


def _coerce_bool(value: Any, *, field: str) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    raise ValueError(f"Machine registry field '{field}' must be boolean")


def _normalize_hardware(entry: dict[str, Any]) -> dict[str, Any]:
    hardware = entry.get("hardware")
    if hardware is None:
        return {}
    if not isinstance(hardware, dict):
        raise ValueError("Machine registry field 'hardware' must be a mapping")

    normalized = dict(hardware)
    for field in (
        "cpu_physical_cores",
        "cpu_logical_cores",
        "memory_gb",
        "disk_total_gb",
        "gpu_vram_gb",
    ):
        if field in normalized:
            normalized[field] = _coerce_number(normalized[field], field=field)

    for field in ("accelerators", "workload_tags"):
        if field in normalized:
            normalized[field] = _coerce_str_list(normalized[field])

    return normalized


def _normalize_storage(value: Any, *, field_prefix: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Machine registry field '{field_prefix}' must be a mapping")

    normalized = dict(value)
    for field in (
        "runner_backing_drive",
        "host_drive",
        "windows_host_path",
        "wsl_distro",
        "vhdx_path",
        "runner_base_dir",
        "disk_bus",
        "disk_media_type",
    ):
        if field in normalized and normalized[field] is not None:
            normalized[field] = str(normalized[field]).strip()

    for field in ("capacity_gb", "free_gb", "cache_budget_gb"):
        if field in normalized:
            normalized[field] = _coerce_number(
                normalized[field],
                field=f"{field_prefix}.{field}",
            )

    return normalized


def _normalize_runner_counts(value: Any, *, field_prefix: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Machine registry field '{field_prefix}' must be a mapping")

    normalized: dict[str, int] = {}
    for field in ("default", "min", "max"):
        if field not in value:
            continue
        count = _coerce_number(value[field], field=f"{field_prefix}.{field}")
        if count is None:
            continue
        if not isinstance(count, int):
            raise ValueError(f"Machine registry field '{field_prefix}.{field}' must be an integer")
        if count < 0:
            raise ValueError(f"Machine registry field '{field_prefix}.{field}' must be non-negative")
        normalized[field] = count
    return normalized


def _normalize_runner_pool_entry(pool: dict[str, Any], *, parent_machine: str) -> dict[str, Any]:
    normalized = dict(pool)

    name = str(normalized.get("name", "")).strip()
    if not name:
        raise ValueError("Machine registry entries require non-empty 'runner_pools.name'")
    normalized["name"] = name
    normalized["parent_machine"] = parent_machine
    normalized["role"] = normalized.get("role") or "runner_pool"

    normalized["aliases"] = _coerce_str_list(normalized.get("aliases"))
    if "runner_labels" in normalized:
        normalized["runner_labels"] = _coerce_str_list(normalized.get("runner_labels"))
    else:
        normalized["runner_labels"] = []

    for field in ("dashboard_url", "storage_tier", "runner_base_dir", "port"):
        if field in normalized and normalized[field] is not None:
            normalized[field] = str(normalized[field]).strip()

    normalized["runners"] = _normalize_runner_counts(
        normalized.get("runners"),
        field_prefix=f"runner_pools.{name}.runners",
    )
    normalized["storage"] = _normalize_storage(
        normalized.get("storage"),
        field_prefix=f"runner_pools.{name}.storage",
    )
    return normalized


def _normalize_runner_pools(entry: dict[str, Any], *, parent_machine: str) -> list[dict[str, Any]]:
    pools = entry.get("runner_pools")
    if pools is None:
        return []
    if not isinstance(pools, list):
        raise ValueError("Machine registry field 'runner_pools' must be a list of mappings")

    normalized: list[dict[str, Any]] = []
    for pool in pools:
        if not isinstance(pool, dict):
            raise ValueError("Each item in 'runner_pools' must be a mapping")
        normalized.append(_normalize_runner_pool_entry(pool, parent_machine=parent_machine))
    return normalized


def _workload_capacity_from_hardware(hardware: dict[str, Any]) -> dict[str, Any]:
    logical = hardware.get("cpu_logical_cores") or 0
    memory_gb = hardware.get("memory_gb") or 0
    vram_gb = hardware.get("gpu_vram_gb") or 0
    tags = set(_coerce_str_list(hardware.get("workload_tags")))
    if vram_gb:
        tags.add("gpu")
    if logical and logical >= 8:
        tags.add("parallel-ci")
    if memory_gb and memory_gb >= 32:
        tags.add("memory-heavy")
    if logical and logical <= 4:
        tags.add("small-ci")

    return {
        "cpu_slots": max(1, int(logical // 2)) if logical else None,
        "memory_gb": memory_gb or None,
        "gpu_vram_gb": vram_gb or None,
        "tags": sorted(tags),
    }


def _merge_known_specs(live_specs: dict[str, Any], registry_specs: dict[str, Any]) -> dict:
    merged = dict(live_specs or {})
    for key, value in (registry_specs or {}).items():
        if value not in (None, "", []):
            merged[key] = value
    return merged


def _load_raw_registry(path: Path) -> dict[str, Any]:
    """Load registry data with security validation.

    Validates path is within allowed roots, checks for symlinks escaping
    allowed directories, and verifies file is not world-writable.
    """
    suffix = path.suffix.lower()

    # See load_machine_registry: the YAML ships next to this module so the
    # module's directory must be an explicit allowed root. The deployed
    # install path is not a git checkout, so the repo-root inference in
    # security.py can't help us.
    explicit_roots = [
        Path(__file__).resolve().parent,
        Path("~/.config/runner-dashboard").expanduser(),
    ]

    if suffix == ".json":
        # For JSON files, still validate path security
        validated_path = validate_config_path(path, allowed_roots=explicit_roots)
        text = validated_path.read_text(encoding="utf-8")
        data = json.loads(text)
    elif yaml is not None:
        # Use secure YAML loader with path validation
        data = safe_yaml_load(path, allowed_roots=explicit_roots)
    else:  # pragma: no cover - kept for bare-bones environments
        validated_path = validate_config_path(path, allowed_roots=explicit_roots)
        text = validated_path.read_text(encoding="utf-8")
        data = json.loads(text)

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Machine registry must be a mapping at the top level")
    return data


def _normalize_machine_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)

    name = str(normalized.get("name", "")).strip()
    if not name:
        raise ValueError("Machine registry entries require a non-empty 'name'")
    normalized["name"] = name

    if "aliases" in normalized:
        normalized["aliases"] = _coerce_str_list(normalized.get("aliases"))
    else:
        normalized["aliases"] = []

    if "runner_labels" in normalized:
        normalized["runner_labels"] = _coerce_str_list(normalized.get("runner_labels"))

    tailscale_nodes = normalized.get("tailscale_nodes")
    if tailscale_nodes is None:
        normalized["tailscale_nodes"] = []
    elif not isinstance(tailscale_nodes, list):
        raise ValueError("Machine registry field 'tailscale_nodes' must be a list of mappings")
    else:
        cleaned_nodes: list[dict[str, Any]] = []
        for node in tailscale_nodes:
            if not isinstance(node, dict):
                raise ValueError("Each item in 'tailscale_nodes' must be a mapping")
            clean_node = dict(node)
            node_name = str(clean_node.get("name", "")).strip()
            if node_name:
                clean_node["name"] = node_name
            ip_addr = str(clean_node.get("ip", "")).strip()
            if ip_addr:
                clean_node["ip"] = ip_addr
            cleaned_nodes.append(clean_node)
        normalized["tailscale_nodes"] = cleaned_nodes

    maintenance = normalized.get("maintenance")
    if maintenance is None:
        normalized["maintenance"] = {}
    elif isinstance(maintenance, dict):
        normalized["maintenance"] = dict(maintenance)
        if "allow_auto_stop" in normalized["maintenance"]:
            normalized["maintenance"]["allow_auto_stop"] = _coerce_bool(
                normalized["maintenance"]["allow_auto_stop"],
                field="maintenance.allow_auto_stop",
            )
    else:
        raise ValueError("Machine registry field 'maintenance' must be a mapping")

    normalized["hardware"] = _normalize_hardware(normalized)
    normalized["workload_capacity"] = _workload_capacity_from_hardware(normalized["hardware"])
    normalized["storage"] = _normalize_storage(normalized.get("storage"), field_prefix="storage")
    normalized["runner_pools"] = _normalize_runner_pools(normalized, parent_machine=name)

    return normalized


def load_machine_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the fleet machine registry.

    Missing files are treated as an empty registry so the dashboard remains
    usable while the foundation is being adopted incrementally.

    Security: Validates that config paths are within allowed roots, rejects
    symlinks pointing outside allowed directories, and refuses world-writable
    config files (issue #355).
    """

    if path is None:
        path = os.environ.get("MACHINE_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH
    registry_path = Path(path)
    if not registry_path.exists():
        return {"version": 1, "machines": []}

    # Validate the path before loading (security check for issue #355).
    #
    # The registry YAML ships alongside this module under ``backend/``, so the
    # directory containing this file is a legitimate allowed root. The deployed
    # install path (``$HOME/actions-runners/dashboard/backend/``) is NOT a git
    # checkout, which means ``_get_repo_root()`` in security.py returns None
    # and the file would otherwise be rejected as "escapes allowed roots".
    # That misconfiguration silently broke fleet federation on every deployed
    # host — the dashboard tried to load this file on every /api/fleet/nodes
    # call and fell back to an empty registry, so cross-machine specs and
    # tailscale_nodes data never reached the UI. See log lines like
    # "Machine registry load failed: Config path escapes allowed roots ...".
    #
    # We explicitly allow:
    #   1. The module's own directory (where this YAML ships)
    #   2. The canonical config dir (~/.config/runner-dashboard/) for sites
    #      that prefer to manage the registry as host config
    #   3. The repo-root config and ~/.config defaults that validate_config_path
    #      already adds
    explicit_roots = [
        Path(__file__).resolve().parent,
        Path("~/.config/runner-dashboard").expanduser(),
    ]
    validate_config_path(registry_path, allowed_roots=explicit_roots)

    raw = _load_raw_registry(registry_path)
    machines = raw.get("machines", [])
    if not isinstance(machines, list):
        raise ValueError("Machine registry field 'machines' must be a list")

    normalized = dict(raw)
    normalized["version"] = int(raw.get("version", 1))
    normalized["machines"] = []
    for entry in machines:
        if not isinstance(entry, dict):
            raise ValueError("Each machine registry entry must be a mapping")
        normalized["machines"].append(_normalize_machine_entry(entry))
    return normalized


def resolve_local_identity(
    name: str | None,
    role: str | None,
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Resolve local node name and role against the machine registry.

    The local node is always matched to the registry by name or alias, NEVER by role.
    If no entry matches, it logs a warning and marks registry_match as 'unregistered'
    instead of borrowing another machine's identity.
    """
    local_name = (name or os.environ.get("DISPLAY_NAME") or platform.node() or "").strip()
    local_role = (role or os.environ.get("MACHINE_ROLE") or "node").strip().lower()

    if not local_name:
        log.warning("Machine registry identity resolution: local node name is empty; marking as unregistered")
        return {
            "name": "",
            "role": local_role,
            "registry_match": "unregistered",
            "matched_alias": None,
        }

    token = _normalize_token(local_name)

    # Search machines first, then runner pools
    for machine in registry.get("machines", []) or []:
        if not isinstance(machine, dict) or machine.get("retired"):
            continue
        m_name = str(machine.get("name", "")).strip()
        if _normalize_token(m_name) == token:
            return {
                "name": local_name,
                "role": machine.get("role") or local_role,
                "registry_match": m_name,
                "matched_alias": _normalize_token(m_name),
                "aliases": list(machine.get("aliases", []) or []),
                "runner_labels": list(machine.get("runner_labels", []) or []),
                "specs": dict(machine.get("hardware", {}) or {}),
                "unregistered": False,
            }
        for alias in machine.get("aliases", []) or []:
            if _normalize_token(str(alias)) == token:
                return {
                    "name": local_name,
                    "role": machine.get("role") or local_role,
                    "registry_match": m_name,
                    "matched_alias": str(alias).strip().lower(),
                    "aliases": list(machine.get("aliases", []) or []),
                    "runner_labels": list(machine.get("runner_labels", []) or []),
                    "specs": dict(machine.get("hardware", {}) or {}),
                    "unregistered": False,
                }
        for pool in machine.get("runner_pools", []) or []:
            if not isinstance(pool, dict) or pool.get("retired"):
                continue
            p_name = str(pool.get("name", "")).strip()
            pool_aliases = list(pool.get("aliases", []) or [])
            pool_labels = list(pool.get("runner_labels", []) or machine.get("runner_labels", []) or [])
            pool_specs = dict(pool.get("hardware", {}) or machine.get("hardware", {}) or {})
            if _normalize_token(p_name) == token:
                return {
                    "name": local_name,
                    "role": pool.get("role") or "runner_pool",
                    "registry_match": p_name,
                    "matched_alias": _normalize_token(p_name),
                    "aliases": pool_aliases,
                    "runner_labels": pool_labels,
                    "specs": pool_specs,
                    "unregistered": False,
                }
            for alias in pool.get("aliases", []) or []:
                if _normalize_token(str(alias)) == token:
                    return {
                        "name": local_name,
                        "role": pool.get("role") or "runner_pool",
                        "registry_match": p_name,
                        "matched_alias": str(alias).strip().lower(),
                        "aliases": pool_aliases,
                        "runner_labels": pool_labels,
                        "specs": pool_specs,
                        "unregistered": False,
                    }

    log.warning("Machine registry has no matching entry for local node %r; marking as unregistered", local_name)
    return {
        "name": local_name,
        "role": local_role,
        "registry_match": "unregistered",
        "matched_alias": None,
        "aliases": [],
        "runner_labels": [],
        "specs": {},
        "unregistered": True,
    }


def build_machine_registry_index(
    registry: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build a lookup table keyed by canonical names and aliases."""

    index: dict[str, dict[str, Any]] = {}
    for entry in registry.get("machines", []):
        if not isinstance(entry, dict):
            continue
        keys = [entry.get("name", ""), *entry.get("aliases", [])]
        for key in keys:
            token = _normalize_token(str(key))
            if token:
                index[token] = entry
        for pool in entry.get("runner_pools", []):
            if not isinstance(pool, dict):
                continue
            pool_dict = dict(pool)
            if not pool_dict.get("parent_machine") and entry.get("name"):
                pool_dict["parent_machine"] = entry.get("name")
            pool_keys = [pool_dict.get("name", ""), *pool_dict.get("aliases", [])]
            for key in pool_keys:
                token = _normalize_token(str(key))
                if token:
                    index[token] = pool_dict
    return index


def _iter_registry_entries(registry: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for machine in registry.get("machines", []):
        if not isinstance(machine, dict) or machine.get("retired"):
            continue
        entries.append(machine)
        for pool in machine.get("runner_pools", []):
            if isinstance(pool, dict) and not pool.get("retired"):
                pool_dict = dict(pool)
                if not pool_dict.get("parent_machine") and machine.get("name"):
                    pool_dict["parent_machine"] = machine.get("name")
                entries.append(pool_dict)
    return entries


def merge_registry_with_live_nodes(
    live_nodes: list[dict[str, Any]],
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge registry metadata into live node payloads.

    Live telemetry wins for status/metrics fields. Registry metadata is exposed
    under the ``registry`` key, and registry-only machines are included as
    offline placeholders so scheduled maintenance can still see them.
    De-duplicates entries where a node appears as both local and remote, or where
    a runner pool entry belongs to an already live parent machine or shares its URL.
    """

    index = build_machine_registry_index(registry)
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_canonical: set[str] = set()
    live_parent_machines: set[str] = set()
    satisfied_parents: set[str] = set()

    # Prioritize local nodes first so local state takes precedence over remote duplicates
    sorted_live = sorted(live_nodes, key=lambda n: not bool(n.get("is_local")))

    for node in sorted_live:
        merged_node = dict(node)
        token = _normalize_token(str(merged_node.get("name", "")))
        registry_entry = index.get(token)
        canonical_name = registry_entry.get("name") if registry_entry else merged_node.get("name")
        canonical_token = _normalize_token(str(canonical_name or ""))

        if canonical_token and canonical_token in seen_canonical:
            # Duplicate entry for this machine (e.g. remote duplicate of local node) -> drop it
            continue
        if canonical_token:
            seen_canonical.add(canonical_token)

        if registry_entry is not None:
            merged_node["registry"] = registry_entry
            hardware_specs = _merge_known_specs(
                merged_node.get("system", {}).get("hardware_specs", {}),
                registry_entry.get("hardware", {}),
            )
            merged_node["hardware_specs"] = hardware_specs
            merged_node["workload_capacity"] = _workload_capacity_from_hardware(hardware_specs)

            entry_name = _normalize_token(str(registry_entry.get("name", "")))
            if entry_name:
                seen.add(entry_name)
            for alias in registry_entry.get("aliases", []) or []:
                seen.add(_normalize_token(str(alias)))

            parent_machine = registry_entry.get("parent_machine")
            if parent_machine:
                satisfied_parents.add(_normalize_token(str(parent_machine)))
            else:
                if entry_name:
                    live_parent_machines.add(entry_name)

        host_vol = (
            merged_node.get("host_volume")
            or merged_node.get("system", {}).get("host_volume")
            or merged_node.get("system", {}).get("disk", {}).get("host_volume")
        )
        if host_vol is not None:
            merged_node["host_volume"] = host_vol
        merged.append(merged_node)

    for entry in _iter_registry_entries(registry):
        if not isinstance(entry, dict):
            continue
        token = _normalize_token(str(entry.get("name", "")))
        if not token or token in seen:
            continue
        parent = entry.get("parent_machine")
        if parent:
            parent_token = _normalize_token(str(parent))
            if parent_token in live_parent_machines:
                continue
        else:
            if token in satisfied_parents:
                continue
        role = entry.get("role", "node")
        if parent and role == "node":
            role = "runner_pool"
        merged.append(
            {
                "name": entry.get("name"),
                "url": entry.get("dashboard_url", ""),
                "online": False,
                "dashboard_reachable": False,
                "is_local": False,
                "role": role,
                "parent_machine": parent,
                "system": {},
                "health": {},
                "hardware_specs": entry.get("hardware", {}),
                "workload_capacity": entry.get("workload_capacity", {}),
                "last_seen": None,
                "error": "Machine is declared in the registry but has no live dashboard.",
                "offline_reason": "dashboard_not_deployed",
                "offline_detail": ("Registry entry exists, but no live dashboard telemetry was returned."),
                "registry": entry,
            }
        )

    return merged
