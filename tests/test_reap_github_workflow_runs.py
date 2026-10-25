"""Wiring tests for scripts/reap_github_workflow_runs.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "merge_queue_entries_graphql.json"


def test_fetch_merge_queue_entries_parses_fixture() -> None:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import reap_github_workflow_runs  # noqa: E402

    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch.object(
        reap_github_workflow_runs,
        "_github_request",
        return_value=(200, json.dumps(body).encode()),
    ):
        entries = reap_github_workflow_runs.fetch_merge_queue_entries("t", "o", "r")
    assert len(entries) == 1
    assert entries[0].head_sha == "0000000000000000000000000000000000000abc"


def test_script_fails_without_token() -> None:
    import subprocess

    root = Path(__file__).resolve().parents[1]
    env = {"PYTHONPATH": str(root), "GITHUB_REPOSITORY": "D-sorganization/Runner_Dashboard"}
    proc = subprocess.run(
        [sys.executable, "scripts/reap_github_workflow_runs.py", "--dry-run", "true"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
