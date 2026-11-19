"""Load and validate resource-shaping trial configuration (#4312)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "resource-shaping-trial.json"

_EXCLUDED_KNOBS = frozenset({"CPUQuota", "MemoryMax"})


def load_trial_config(path: Path | None = None) -> dict[str, Any]:
    """Load trial JSON; returns a deep copy dict."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("resource-shaping config must be a JSON object")
    validate_trial_config(raw)
    return json.loads(json.dumps(raw))


def validate_trial_config(cfg: dict[str, Any]) -> None:
    """Reject shapes that would apply excluded systemd hard caps."""
    if not isinstance(cfg.get("enabled"), bool):
        raise ValueError("enabled must be a boolean")
    systemd = cfg.get("systemd")
    if not isinstance(systemd, dict):
        raise ValueError("systemd must be an object")
    forbidden = {
        "cpuquota": "CPUQuota",
        "cpu_quota": "CPUQuota",
        "memorymax": "MemoryMax",
        "memory_max": "MemoryMax",
        "CPUQuota": "CPUQuota",
        "MemoryMax": "MemoryMax",
    }
    for key, label in forbidden.items():
        if key in systemd:
            raise ValueError(f"excluded knob present: {label}")
    for key, lower, upper in (("cpu_weight", 1, 10000), ("io_weight", 1, 10000), ("nice", -20, 19)):
        val = systemd.get(key)
        if type(val) is not int or not lower <= val <= upper:
            raise ValueError(f"systemd.{key} must be an integer in {lower}..{upper}")
    if not isinstance(systemd.get("memory_high_enabled"), bool):
        raise ValueError("systemd.memory_high_enabled must be a boolean")
    slice_name = systemd.get("slice_name")
    if not isinstance(slice_name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+\.slice", slice_name):
        raise ValueError("systemd.slice_name must be a slice unit name")
    memory_high = systemd.get("memory_high")
    if not isinstance(memory_high, str) or not re.fullmatch(r"[1-9][0-9]*[KMGTPE]?", memory_high):
        raise ValueError("systemd.memory_high must be a positive systemd size")
    measurement = cfg.get("measurement")
    if not isinstance(measurement, dict):
        raise ValueError("measurement must be an object")
