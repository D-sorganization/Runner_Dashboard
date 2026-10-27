#!/usr/bin/env python3
"""Idempotently create priority:expedite in config/projects.json repos."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ORG, LABEL = "D-sorganization", "priority:expedite"
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    repos = [r for r in json.loads((ROOT / "config/projects.json").read_text())["repos"] if isinstance(r, str)]
    fail = 0
    for name in repos:
        cmd = ["gh", "label", "create", LABEL, "--repo", f"{ORG}/{name}", "--color", "B60205", "--force"]
        fail += subprocess.run(cmd, check=False).returncode != 0
    return fail


if __name__ == "__main__":
    sys.exit(main())
