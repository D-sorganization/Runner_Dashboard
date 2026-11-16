"""Render validated operator settings into trial systemd drop-ins."""

from __future__ import annotations

import sys
from pathlib import Path

from deploy.resource_shaping_trial.config import load_trial_config


def render_dropins(config_path: Path, output_dir: Path) -> None:
    """Validate before writing; systemd values cannot contain injected directives."""
    settings = load_trial_config(config_path)["systemd"]
    service = (
        f"[Service]\nSlice={settings['slice_name']}\n"
        f"CPUWeight={settings['cpu_weight']}\nIOWeight={settings['io_weight']}\n"
        f"Nice={settings['nice']}\n"
    )
    (output_dir / "15-runner-resource-shaping.conf").write_text(service, encoding="utf-8")
    (output_dir / "15-runner-resource-shaping-memory-high.conf").write_text(
        f"[Service]\nMemoryHigh={settings['memory_high']}\n", encoding="utf-8"
    )


if __name__ == "__main__":
    render_dropins(Path(sys.argv[1]), Path(sys.argv[2]))
