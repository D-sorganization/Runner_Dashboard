"""One spec-check workflow per repository (Repository_Management#1916).

Runner_Dashboard carried two near-identical workflows, ``ci-spec-check.yml``
("Spec Check") and ``spec-check-enhanced.yml`` ("Spec Check (Enhanced)"). Both
started on every PR event for the same signal. The enhanced copy is folded
into ``ci-spec-check.yml`` and removed; these tests pin every check the removed
file performed onto the survivor, by executing the survivor's detection step
against a stubbed ``git diff``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_SPEC_CHECK = _WORKFLOWS / "ci-spec-check.yml"
_REMOVED = "spec-check-enhanced.yml"

# Every source pattern the removed workflow treated as "needs a SPEC.md update".
_REMOVED_SOURCE_PATHS = (
    "src/module.py",
    "tests/test_module.py",
    "config/settings.json",
    "pyproject.toml",
    "Cargo.toml",
    "package.json",
    "requirements.txt",
)


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(data: dict) -> dict:
    triggers = data.get(True) or data.get("on")
    assert isinstance(triggers, dict)
    return triggers


def _detect(files: list[str]) -> dict[str, str]:
    """Run the survivor's detection step with ``git diff`` returning ``files``."""
    job = _load(_SPEC_CHECK)["jobs"]["spec-freshness"]
    step = next(s for s in job["steps"] if s.get("id") == "check")
    script = step["run"].replace("${{ github.base_ref }}", "main")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fake_git = tmp_path / "git"
        fake_git.write_text('#!/usr/bin/env bash\nprintf "%s\\n" $FAKE_FILES\n', encoding="utf-8")
        fake_git.chmod(0o755)
        output = tmp_path / "out"
        output.write_text("", encoding="utf-8")
        env = {
            "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
            "GITHUB_OUTPUT": str(output),
            "FAKE_FILES": " ".join(files),
        }
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        pairs = (line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)
        return dict(pairs)


def test_exactly_one_spec_check_workflow() -> None:
    names = [
        path.name
        for path in sorted(_WORKFLOWS.glob("*.yml"))
        if str(_load(path).get("name", "")).startswith("Spec Check")
    ]
    assert names == ["ci-spec-check.yml"], f"expected one spec-check workflow, found {names}"
    assert not (_WORKFLOWS / _REMOVED).exists()


def test_removed_workflow_is_not_allowlisted_anywhere() -> None:
    guard = (_WORKFLOWS / "local-only-runner-guard.yml").read_text(encoding="utf-8")
    assert _REMOVED not in guard
    routing = json.loads((_ROOT / "config" / "workflow_runner_routing_policy.json").read_text(encoding="utf-8"))
    assert _REMOVED not in json.dumps(routing)


def test_survivor_keeps_name_and_label_triggers() -> None:
    data = _load(_SPEC_CHECK)
    assert data["name"] == "Spec Check"
    types = _triggers(data)["pull_request"]["types"]
    for event_type in ("opened", "synchronize", "reopened", "labeled", "unlabeled"):
        assert event_type in types


def test_survivor_honours_spec_exempt_label() -> None:
    job = _load(_SPEC_CHECK)["jobs"]["spec-freshness"]
    assert "spec-exempt" in str(job["if"])


@pytest.mark.parametrize("path", _REMOVED_SOURCE_PATHS)
def test_survivor_covers_every_pattern_of_the_removed_workflow(path: str) -> None:
    assert _detect([path])["needs_update"] == "true"
    assert _detect([path, "SPEC.md"])["needs_update"] == "false"


def test_docs_only_change_needs_no_spec_update() -> None:
    outputs = _detect(["docs/guide.md", "README.md"])
    assert outputs["source_changed"] == "false"
    assert outputs["needs_update"] == "false"


def test_survivor_fails_when_spec_is_stale() -> None:
    job = _load(_SPEC_CHECK)["jobs"]["spec-freshness"]
    step = next(s for s in job["steps"] if s.get("name") == "Fail if spec is stale")
    assert step["if"] == "steps.check.outputs.needs_update == 'true'"
    assert "exit 1" in step["run"]


# Runner_Dashboard#1871: backend changes must update SPEC.md (CLAUDE.md, Spec Check).
@pytest.mark.parametrize("path", ["backend/server.py", "backend/routers/fleet.py"])
def test_backend_change_needs_spec_update(path: str) -> None:
    assert _detect([path])["needs_update"] == "true"
    assert _detect([path, "SPEC.md"])["needs_update"] == "false"


def _comment_script() -> str:
    job = _load(_SPEC_CHECK)["jobs"]["spec-freshness"]
    step = next(s for s in job["steps"] if s.get("name") == "Post warning comment")
    return str(step["with"]["script"])


def test_comment_does_not_tell_authors_to_bump_spec_version() -> None:
    """Spec Version is release-derived (Repository_Management#1520)."""
    script = _comment_script()
    assert "Bump the Spec Version" not in script
    assert "Do NOT bump the \\`Spec Version\\` field" in script
    assert "change-log row keyed by this PR" in script
