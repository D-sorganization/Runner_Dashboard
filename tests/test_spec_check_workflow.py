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
import shutil
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


# Codex P2 on #1872: a stale bot warning must be edited, not left in place.
_NODE_HARNESS = r"""
const script = require('fs').readFileSync(process.argv[2], 'utf8');
const existing = JSON.parse(process.argv[3]);
const calls = [];
const github = {
  rest: { issues: {
    listComments: async () => ({ data: existing }),
    createComment: async (a) => { calls.push({ op: 'create', body: a.body }); },
    updateComment: async (a) => { calls.push({ op: 'update', id: a.comment_id, body: a.body }); },
  } },
  paginate: async (fn, args) => (await fn(args)).data,
};
const context = { repo: { owner: 'o', repo: 'r' }, issue: { number: 1 } };
const run = new Function('github', 'context', `return (async () => {${script}})();`);
run(github, context).then(() => console.log(JSON.stringify(calls)));
"""


def _run_comment_step(existing: list[dict]) -> list[dict]:
    """Execute the comment step's script under node with a stubbed ``github``."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "script.js"
        script.write_text(_comment_script(), encoding="utf-8")
        harness = Path(tmp) / "harness.js"
        harness.write_text(_NODE_HARNESS, encoding="utf-8")
        result = subprocess.run(
            [node, str(harness), str(script), json.dumps(existing)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    assert result.returncode == 0, result.stderr
    calls = json.loads(result.stdout)
    assert isinstance(calls, list)
    return calls


def _bot(comment_id: int, body: str, login: str = "github-actions[bot]") -> dict:
    return {"id": comment_id, "body": body, "user": {"type": "Bot", "login": login}}


def test_comment_created_when_none_exists() -> None:
    calls = _run_comment_step([])
    assert [c["op"] for c in calls] == ["create"]
    assert "SPEC.md Update Required" in calls[0]["body"]


def test_stale_bot_comment_is_updated_in_place() -> None:
    stale = "## ⚠️ SPEC.md Update Required\n\n- Bump the Spec Version"
    calls = _run_comment_step([_bot(7, stale)])
    assert [(c["op"], c.get("id")) for c in calls] == [("update", 7)]
    assert "Bump the Spec Version" not in calls[0]["body"]


def test_identical_bot_comment_is_left_alone() -> None:
    body = _run_comment_step([])[0]["body"]
    assert _run_comment_step([_bot(7, body)]) == []


def test_non_bot_comment_is_never_edited() -> None:
    human = {"id": 3, "body": "SPEC.md Update Required?", "user": {"type": "User"}}
    calls = _run_comment_step([human])
    assert [c["op"] for c in calls] == ["create"]


def test_other_bots_warning_text_is_never_edited() -> None:
    """Codex P2 on #1877: only this workflow's own comment may be replaced."""
    other = _bot(9, "Reviewer note: SPEC.md Update Required here", "codex[bot]")
    calls = _run_comment_step([other])
    assert [c["op"] for c in calls] == ["create"]


def test_created_comment_carries_the_workflow_marker() -> None:
    body = _run_comment_step([])[0]["body"]
    assert body.startswith("<!-- spec-check:warning -->")


def test_marked_comment_is_found_even_if_heading_changes() -> None:
    marked = "<!-- spec-check:warning -->\nold wording"
    calls = _run_comment_step([_bot(5, marked)])
    assert [(c["op"], c.get("id")) for c in calls] == [("update", 5)]


# Repository_Management#1894 (RM-5): a per-PR change fragment stands in for the
# SPEC.md edit; collate-changes.yml writes the PR-keyed row after merge.
@pytest.mark.parametrize("fragment", ["changes/1894.md", "changes/1894-rd-fragments.md"])
def test_change_fragment_satisfies_the_spec_check(fragment: str) -> None:
    outputs = _detect(["backend/server.py", fragment])
    assert outputs["spec_changed"] == "true"
    assert outputs["needs_update"] == "false"


@pytest.mark.parametrize(
    "not_a_fragment",
    [
        "changes/README.md",
        "changes/notes.md",
        "changes/Not-Lowercase.md",
        "changes/1894-x.txt",
        "changes/sub/1894-x.md",
        "docs/changes/1894-x.md",
    ],
)
def test_only_a_real_change_fragment_stands_in_for_spec(not_a_fragment: str) -> None:
    assert _detect(["backend/server.py", not_a_fragment])["needs_update"] == "true"


def test_warning_comment_offers_the_fragment() -> None:
    script = _comment_script()
    assert "changes/<issue>-<slug>.md" in script
    assert "scripts/changes_fragment.py new" in script
