#!/usr/bin/env python3
"""Pre-push regeneration hook: refresh API contract artifacts when the diff touches them."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from scripts.verify_merge_gates import touches_api_contract  # noqa: PLC0415
    from scripts.run_mypy_diff import resolve_changed_files  # noqa: PLC0415

    if os.environ.get("FLEET_HOOK_FROM_REF") and os.environ.get("FLEET_HOOK_TO_REF"):
        diff = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                f"{os.environ['FLEET_HOOK_FROM_REF']}..{os.environ['FLEET_HOOK_TO_REF']}",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode != 0:
            sys.stderr.write("agent-check-regen: could not read pre-push ref range\n")
            return diff.returncode
        changed = diff.stdout.splitlines()
    else:
        changed = resolve_changed_files(cwd=root)
    changed = [path.replace("\\", "/") for path in changed]
    if not touches_api_contract(changed):
        return 0

    proc = subprocess.run(
        ["bash", "scripts/gen-api-client.sh"],
        cwd=root,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write("agent-check-regen: gen-api-client.sh failed\n")
    else:
        sys.stdout.write(
            "agent-check-regen: regenerated frontend/src/lib/openapi.json and api-types.ts\n"
        )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
