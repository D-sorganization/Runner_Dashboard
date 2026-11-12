#!/usr/bin/env python3
"""Validate or execute the opt-in private CI gate manifest."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "ci" / "private-gates.yml"
EXPECTED_IDS = (
    "lint",
    "types",
    "tests",
    "coverage",
    "secret-scan",
    "secret-baseline",
)
log = logging.getLogger(__name__)


def load_manifest(path: Path = MANIFEST) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"gate manifest is missing: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unsupported private CI manifest schema")
    if data.get("feature_flag") != "private_ci":
        raise ValueError("private CI manifest must use the private_ci flag")
    gates = data.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ValueError("private CI gates must be a non-empty list")
    ids = tuple(gate.get("id") for gate in gates if isinstance(gate, dict))
    if ids != EXPECTED_IDS or len(ids) != len(gates):
        raise ValueError("private CI gates have invalid ids or ordering")
    for gate in gates:
        if not isinstance(gate.get("tool"), str) or not gate.get("tool"):
            raise ValueError("each gate requires a tool")
        if not isinstance(gate.get("version"), str) or not gate.get("version"):
            raise ValueError("each gate requires a pinned tool version")
        command = gate.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("each gate requires a non-empty command")
        if gate["tool"] == "detect-secrets":
            hook_config = REPO_ROOT / ".pre-commit-config.yaml"
            hook_text = hook_config.read_text(encoding="utf-8")
            pinned_hook = re.search(
                r"- repo: https://github\.com/Yelp/detect-secrets"
                r"(?:(?!\n  - repo:).)*?\n\s+rev: [0-9a-f]{40}\b",
                hook_text,
                flags=re.DOTALL,
            )
            if f"# v{gate['version']}" not in hook_text or not pinned_hook:
                raise ValueError("secret scanner version must match its pinned hook")
        elif gate["tool"] == "gitleaks":
            secrets_workflow = REPO_ROOT / ".github/workflows/ci-secrets.yml"
            if f'GITLEAKS_VERSION: "{gate["version"]}"' not in secrets_workflow.read_text(encoding="utf-8"):
                raise ValueError("gitleaks version must match the CI Secrets workflow")
        elif f"{gate['tool']}=={gate['version']}" not in " ".join(command):
            raise ValueError(f"gate {gate['id']} command must pin its tool version")
    return gates


def run_gates(
    gates: Sequence[dict[str, Any]],
    *,
    runner: Callable[[Sequence[str]], int] | None = None,
) -> int:
    """Run gates in manifest order and stop at the first failing gate."""
    execute = runner or (lambda command: subprocess.run(command, check=False, cwd=REPO_ROOT).returncode)
    for gate in gates:
        try:
            code = execute(gate["command"])
        except OSError as exc:
            log.error("private CI gate could not start (%s): %s", gate["id"], exc)
            return 127
        if code:
            log.error("private CI gate failed: %s", gate["id"])
            return code
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args not in (["--validate"], ["--run"]):
        log.error("usage: python -m backend.ci.private_gate_manifest --validate|--run")
        return 2
    try:
        gates = load_manifest()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.error("invalid private CI manifest: %s", exc)
        return 2
    if args == ["--validate"]:
        log.info("private CI gate manifest is valid")
        return 0
    if os.environ.get("PRIVATE_CI", "").lower() not in {"1", "true", "yes"}:
        log.info("private CI is disabled; no gates were run")
        return 0
    return run_gates(gates)


if __name__ == "__main__":
    raise SystemExit(main())
