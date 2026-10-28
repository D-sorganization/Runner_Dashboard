"""merge_group ref guard: obsolete queue entries self-cancel before heavy jobs (#2764 F1)."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from bash_host import find_bash

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / ".github" / "scripts" / "merge-queue-ref-guard.sh"
_ACTION = "./.github/actions/merge-queue-ref-guard"
_WORKFLOWS = _ROOT / ".github" / "workflows"

_MERGE_GROUP_WORKFLOWS = [
    "ci-standard.yml",
    "frontend-tests.yml",
    "docker-build.yml",
    "ci-secrets.yml",
    "ci-auto-deploy-regression.yml",
]

_STANDALONE_GUARD_JOB_WORKFLOWS = [
    "docker-build.yml",
    "ci-auto-deploy-regression.yml",
]

_GUARD_STEP_ENTRY_JOBS: dict[str, str] = {
    "ci-standard.yml": "pick-runner",
    "frontend-tests.yml": "frontend-scope",
    "ci-secrets.yml": "detect-secrets",
}

_QUEUE_REF = "gh-readonly-queue/main/pr-77-abcdef0123456789abcdef0123456789"  # pragma: allowlist secret


def _load_workflow(name: str) -> dict:
    data = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _job_needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    if isinstance(needs, str):
        return [needs]
    return list(needs)


def _guard_script_from_action() -> str:
    action = yaml.safe_load(
        (_ROOT / ".github" / "actions" / "merge-queue-ref-guard" / "action.yml").read_text(encoding="utf-8")
    )
    steps = action["runs"]["steps"]
    run_step = next(s for s in steps if "merge-queue-ref-guard" in str(s.get("run", "")))
    return str(run_step["run"])


def _run_guard(
    *,
    gh_stub: str,
    event_name: str = "merge_group",
    head_ref: str = _QUEUE_REF,
    run_id: str = "999001",
    repository: str = "D-sorganization/Runner_Dashboard",
) -> subprocess.CompletedProcess[str]:
    assert _SCRIPT.is_file(), "guard script must exist before workflow wiring"
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_EVENT_NAME": event_name,
            "MERGE_GROUP_HEAD_REF": head_ref,
            "GITHUB_REF": f"refs/heads/{head_ref}",
            "GITHUB_RUN_ID": run_id,
            "GITHUB_REPOSITORY": repository,
            "PATH": f"{Path(gh_stub).parent}:{env.get('PATH', '')}",
        }
    )
    bash = find_bash()
    assert bash is not None
    return subprocess.run(
        [bash, str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("name", _MERGE_GROUP_WORKFLOWS)
def test_pull_request_jobs_never_need_queue_entry_alive_job(name: str) -> None:
    """Skipped queue-entry-alive must not skip changes, scope, or health jobs on pull_request."""
    workflow = _load_workflow(name)
    for job_id, job in workflow["jobs"].items():
        assert "queue-entry-alive" not in _job_needs(job), (
            f"{name}::{job_id} must not need queue-entry-alive (transitive skip on pull_request)"
        )


@pytest.mark.parametrize("name", _STANDALONE_GUARD_JOB_WORKFLOWS)
def test_merge_group_only_workflows_keep_standalone_guard_job(name: str) -> None:
    workflow = _load_workflow(name)
    job = workflow["jobs"]["queue-entry-alive"]
    assert "github.event_name == 'merge_group'" in str(job.get("if", ""))
    assert job["runs-on"] == "d-sorg-fleet"
    assert job["permissions"] == {"actions": "write", "contents": "read"}


def _guard_steps_from_job(steps: list) -> list[dict]:
    return [s for s in steps if "merge-queue-ref-guard" in str(s.get("run", "")) or _ACTION in str(s.get("uses", ""))]


@pytest.mark.parametrize("name", sorted(_GUARD_STEP_ENTRY_JOBS))
def test_merge_group_guard_step_on_first_heavy_job(name: str) -> None:
    job_id = _GUARD_STEP_ENTRY_JOBS[name]
    steps = _load_workflow(name)["jobs"][job_id]["steps"]
    guard_steps = _guard_steps_from_job(steps)
    assert guard_steps, f"{name}::{job_id} must run merge-queue-ref-guard on merge_group"
    guard = guard_steps[0]
    assert "merge_group" in str(guard.get("if", ""))


def test_composite_action_invokes_shared_guard_script() -> None:
    assert "merge-queue-ref-guard.sh" in _guard_script_from_action()


def test_existing_queue_ref_continues(tmp_path: Path) -> None:
    gh = tmp_path / "gh"
    gh.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            echo "$@" >> "{tmp_path / "calls.log"}"
            if [[ "$1" == "api" ]]; then
              exit 0
            fi
            echo "unexpected gh invocation: $*" >&2
            exit 99
            """
        ),
        encoding="utf-8",
    )
    gh.chmod(0o755)

    result = _run_guard(gh_stub=str(gh))
    assert result.returncode == 0
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "git/ref/" in log
    assert "run cancel" not in log


def test_missing_queue_ref_self_cancels_only_this_run(tmp_path: Path) -> None:
    gh = tmp_path / "gh"
    gh.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            echo "$@" >> "{tmp_path / "calls.log"}"
            if [[ "$1" == "api" ]]; then
              echo '{{"message":"Not Found"}}' >&2
              exit 1
            fi
            if [[ "$1" == "run" && "$2" == "cancel" ]]; then
              if [[ "$3" != "999001" ]]; then
                echo "refusing to cancel another run: $3" >&2
                exit 2
              fi
              exit 0
            fi
            if [[ "$1" == "run" && "$2" == "view" ]]; then
              echo '{{"status":"completed","conclusion":"cancelled"}}'
              exit 0
            fi
            exit 99
            """
        ),
        encoding="utf-8",
    )
    gh.chmod(0o755)

    result = _run_guard(gh_stub=str(gh))
    assert result.returncode == 0
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "run cancel 999001" in log
    assert "run cancel" not in log.replace("run cancel 999001", "")


@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_non_merge_group_events_are_never_touched(event_name: str, tmp_path: Path) -> None:
    gh = tmp_path / "gh"
    gh.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    gh.chmod(0o755)

    result = _run_guard(gh_stub=str(gh), event_name=event_name, head_ref="main")
    assert result.returncode == 0


@pytest.mark.parametrize("name", _MERGE_GROUP_WORKFLOWS)
def test_workflow_does_not_grant_actions_write_globally(name: str) -> None:
    workflow = _load_workflow(name)
    top = workflow.get("permissions")
    if top is None:
        return
    if isinstance(top, dict):
        assert top.get("actions") != "write", f"{name}: actions: write belongs on guard jobs/steps only"


def test_ci_standard_tests_job_can_self_cancel_on_merge_group_recheck() -> None:
    perms = _load_workflow("ci-standard.yml")["jobs"]["tests"]["permissions"]
    assert perms == {"actions": "write", "contents": "read"}


def test_frontend_scope_guard_preserves_pull_request_files_read_permission() -> None:
    workflow = _load_workflow("frontend-tests.yml")

    assert workflow["jobs"]["frontend-scope"]["permissions"] == {
        "actions": "write",
        "contents": "read",
        "pull-requests": "read",
    }


def test_frontend_scope_merge_group_exits_before_pr_number_lookup() -> None:
    """merge_group must not call the GitHub PR files API (no pull_request payload)."""
    scope_step = next(
        s
        for s in _load_workflow("frontend-tests.yml")["jobs"]["frontend-scope"]["steps"]
        if s.get("id") == "scope"
    )
    run = str(scope_step["run"])
    merge_block = run.split('if [ "$EVENT_NAME" = "merge_group" ]; then', 1)[1].split("fi", 1)[0]
    assert "run_frontend_tests=true" in merge_block
    assert "gh pr" not in merge_block
    assert run.index("merge_group") < run.index("PR_NUMBER")


def test_gh_api_error_fails_open_without_cancel(tmp_path: Path) -> None:
    gh = tmp_path / "gh"
    gh.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            echo "$@" >> "{tmp_path / "calls.log"}"
            if [[ "$1" == "api" ]]; then
              echo "internal error" >&2
              exit 1
            fi
            exit 99
            """
        ),
        encoding="utf-8",
    )
    gh.chmod(0o755)

    result = _run_guard(gh_stub=str(gh))
    assert result.returncode == 0
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "run cancel" not in log
    assert "fail-open" in result.stderr + result.stdout
