"""Regression tests for native-language reads of head-bearing env aliases."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import fork_pr_runner_guard as guard
from scripts.fork_pr_context_refs import render_literal_interpolations
from scripts.fork_pr_guard_analysis import reads_head

pytestmark = pytest.mark.unit
NATIVE_READS = [
    "const head = process.env.HEAD;",
    'const head = process.env["HEAD"];',
    "const head = process.env [ 'HEAD' ];",
    "const head = process . env . HEAD;",
    "const head = process.env.head;",
    "${{ 'const head = process.env.HEAD;' }}",
    "${{ 'const head = process.env[\"HEAD\"];' }}",
    "${{ 'const head = process.env[''HEAD''];' }}",
    "${{ 'const head = process[''env''][''HEAD''];' }}",
    "${{ 'const head = process . env . HEAD;' }}",
    "${{ 'const head = process.env.head;' }}",
    "${{ 'const env = process.env; const head = env.HEAD;' }}",
    "const head = process.${{ 'env.HEAD' }};",
    "const head = ${{ 'process' }}.${{ 'env' }}.${{ 'HEAD' }};",
    "const head = process[${{ '\"env\"' }}][${{ '\"HEAD\"' }}];",
    "const head = process.env.${{ 'head' }};",
    "const env = process.env; const head = env.${{ 'HEAD' }};",
]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("process.${{ 'env.HEAD' }}", "process.env.HEAD"),
        ("${{ 'it''s' }}", "it's"),
        ("en${{ inputs.dynamic }}v.HEAD", "en\x00v.HEAD"),
        ("${{ 'env.HEAD' || inputs.other }}", "\x00"),
        ("${{ format('env.HEAD') }}", "\x00"),
        ("${{ 'env.HEAD'", "\x00"),
    ],
)
def test_literal_renderer_preserves_neighbors_without_evaluating_expressions(source: str, expected: str) -> None:
    assert render_literal_interpolations(source) == expected


@pytest.mark.parametrize("script", NATIVE_READS)
def test_head_alias_reads_through_node_process_env(script: str) -> None:
    """A native JS read consumes the same env alias as a shell reference."""
    assert reads_head(script, aliases=("HEAD",))


@pytest.mark.parametrize(
    "script",
    [
        "const payload = process.env.PAYLOAD;",
        "const head = process.env.HEAD_SUFFIX;",
        "const head = process.env.XHEAD;",
        "const api = github; await api.rest.issues.listForRepo({});",
        "${{ 'env.HEAD' }}",
        "${{ 'const value = process.env.PAYLOAD;' }}",
        "const head = process.${{ 'env.PAYLOAD' }};",
        "const head = process.${{ 'env.HEAD_SUFFIX' }};",
        "const head = process.env.${{ inputs.key }}HEAD;",
    ],
)
def test_other_native_env_names_and_github_script_client_are_allowed(
    script: str,
) -> None:
    assert not reads_head(script, aliases=("HEAD",))


def test_native_env_read_without_head_alias_is_allowed() -> None:
    assert not reads_head("const value = process.env.HEAD;")


@pytest.mark.parametrize("event", ["workflow_run", "workflow_call"])
@pytest.mark.parametrize("script", NATIVE_READS)
def test_privileged_self_hosted_workflow_rejects_native_head_env_read(tmp_path: Path, event: str, script: str) -> None:
    workflow = {
        "on": {event: {}},
        "env": {"HEAD": "${{ github.event.workflow_run.head_sha }}"},
        "jobs": {
            "consume": {
                "runs-on": "d-sorg-fleet",
                "steps": [
                    {
                        "uses": "actions/github-script@v7",
                        "with": {"script": script},
                    }
                ],
            }
        },
    }
    if event == "workflow_call":
        workflow["env"]["HEAD"] = "${{ github.event.pull_request.head.sha }}"
        workflow["jobs"]["consume"]["if"] = "github.event_name == 'pull_request_target'"
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "native-env.yml").write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    violations = guard.find_violations(workflow_dir)
    assert len(violations) == 1
    assert "checks out PR head code" in violations[0]


def test_native_github_script_client_without_alias_read_remains_allowed(
    tmp_path: Path,
) -> None:
    workflow = {
        "on": {"workflow_run": {}},
        "env": {"HEAD": "${{ github.event.workflow_run.head_sha }}"},
        "jobs": {
            "consume": {
                "runs-on": "d-sorg-fleet",
                "steps": [
                    {
                        "uses": "actions/github-script@v7",
                        "with": {"script": ("const api = github; await api.rest.issues.listForRepo({});")},
                    }
                ],
            }
        },
    }
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "client-only.yml").write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    assert guard.find_violations(workflow_dir) == []
