"""Load and validate resource-shaping trial configuration (#4312)."""

from __future__ import annotations

import json
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
    for weight_key in ("cpu_weight", "io_weight", "nice"):
        val = systemd.get(weight_key)
        if not isinstance(val, int) or val < 1:
            raise ValueError(f"systemd.{weight_key} must be a positive integer")
    measurement = cfg.get("measurement")
    if not isinstance(measurement, dict):
        raise ValueError("measurement must be an object")
