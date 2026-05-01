#!/usr/bin/env python3
"""Fleet machine registry loading and merge helpers.

The registry is stored as repo-managed YAML or JSON beside the dashboard
backend. It gives the dashboard and scheduled maintenance jobs a canonical
source of truth for machine identity, aliases, roles, and maintenance hints.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - deployment installs PyYAML
    yaml = None  # type: ignore[assignment]

from security import safe_yaml_load, validate_config_path

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

    if suffix == ".json":
        # For JSON files, still validate path security
        validated_path = validate_config_path(path)
        text = validated_path.read_text(encoding="utf-8")
        data = json.loads(text)
    elif yaml is not None:
        # Use secure YAML loader with path validation
        data = safe_yaml_load(path)
    else:  # pragma: no cover - kept for bare-bones environments
        validated_path = validate_config_path(path)
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

    # Validate the path before loading (security check for issue #355)
    # This will raise ValueError if path escapes allowed roots, is a dangerous
    # symlink, or is world-writable
    validate_config_path(registry_path)

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
    return index


def merge_registry_with_live_nodes(
    live_nodes: list[dict[str, Any]],
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge registry metadata into live node payloads.

    Live telemetry wins for status/metrics fields. Registry metadata is exposed
    under the ``registry`` key, and registry-only machines are included as
    offline placeholders so scheduled maintenance can still see them.
    """

    index = build_machine_registry_index(registry)
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for node in live_nodes:
        merged_node = dict(node)
        token = _normalize_token(str(merged_node.get("name", "")))
        registry_entry = index.get(token)
        if registry_entry is not None:
            merged_node["registry"] = registry_entry
            hardware_specs = _merge_known_specs(
                merged_node.get("system", {}).get("hardware_specs", {}),
                registry_entry.get("hardware", {}),
            )
            merged_node["hardware_specs"] = hardware_specs
            merged_node["workload_capacity"] = _workload_capacity_from_hardware(hardware_specs)
            seen.add(_normalize_token(str(registry_entry.get("name", ""))))
        merged.append(merged_node)

    for entry in registry.get("machines", []):
        if not isinstance(entry, dict):
            continue
        token = _normalize_token(str(entry.get("name", "")))
        if not token or token in seen:
            continue
        merged.append(
            {
                "name": entry.get("name"),
                "url": entry.get("dashboard_url", ""),
                "online": False,
                "dashboard_reachable": False,
                "is_local": False,
                "role": entry.get("role", "node"),
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
