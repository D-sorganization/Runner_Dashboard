"""Reversible CI runner selection for GitHub Actions (issue hybrid fast-checks).

Private repositories default to the self-hosted fleet. Public repos and fork PRs
use GitHub-hosted runners via ``CI_RUNNER_MODE``. When ``RD_FAST_CHECKS_RUNNER``
is ``hosted``, only allowlisted required-check jobs may use hosted capacity on
private repos for ``merge_group`` events (PR sync stays on the fleet).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Literal

RD_FAST_CHECKS_VAR: Final = "RD_FAST_CHECKS_RUNNER"
VALID_RD_FAST_CHECKS_VALUES: Final[frozenset[str]] = frozenset({"", "hosted", "local"})

_FORK_ROUTE: Final = (
    "github.event.pull_request && "
    "github.event.pull_request.head.repo.full_name != github.repository && "
    "'ubuntu-latest' || "
)

_PUBLIC_HOSTED_TAIL: Final = (
    "!github.event.repository.private && vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest' "
    "|| 'd-sorg-fleet'"
)

_QUEUE_AND_PR_HOSTED: Final = (
    "github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_MODE == 'hosted' && 'ubuntu-latest' "
    "|| github.event_name == 'pull_request' && vars.CI_PR_RUNNER_MODE == 'hosted' && 'ubuntu-latest' "
)

_MERGE_GROUP_FAST_CHECKS: Final = (
    f"github.event.repository.private && github.event_name == 'merge_group' "
    f"&& vars.{RD_FAST_CHECKS_VAR} == 'hosted' && 'ubuntu-latest'"
)

_PRIVATE_FAST_CHECKS_TAIL: Final = (
    "!github.event.repository.private && vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest' "
    f"|| {_QUEUE_AND_PR_HOSTED}"
    f"|| {_MERGE_GROUP_FAST_CHECKS} "
    "|| github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_LABEL "
    "|| 'd-sorg-fleet'"
)

_FRONTEND_FAST_CHECKS_TAIL: Final = (
    "!github.event.repository.private && vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest' "
    f"|| {_QUEUE_AND_PR_HOSTED}"
    f"|| {_MERGE_GROUP_FAST_CHECKS} "
    "|| github.event_name == 'merge_group' && vars.CI_QUEUE_RUNNER_LABEL "
    "|| vars.CI_BROWSER_RUNNER_LABEL || 'd-sorg-fleet'"
)

_WORKFLOW_NAME_TO_FILE: Final[dict[str, str]] = {
    "CI Standard": "ci-standard.yml",
    "Frontend Tests": "frontend-tests.yml",
    "CI Secrets": "ci-secrets.yml",  # pragma: allowlist secret
    "Anti-Phantom Merge Guard": "anti-phantom-merge.yml",
}


def assert_valid_rd_fast_checks_mode(value: str | None) -> None:
    """Precondition: repository variable is unset, ``hosted``, or ``local`` only."""
    if value is None:
        return
    if value not in VALID_RD_FAST_CHECKS_VALUES:
        raise ValueError(
            f"{RD_FAST_CHECKS_VAR} must be 'hosted', 'local', or unset; got {value!r}"
        )


def default_runs_on_expression() -> str:
    """Workflow ``runs-on`` for fleet-default jobs (no private fast-check opt-in)."""
    return f"${{{{ {_FORK_ROUTE}{_PUBLIC_HOSTED_TAIL} }}}}"


def required_check_runs_on_expression() -> str:
    """Workflow ``runs-on`` for required status-check jobs on private repos."""
    return f"${{{{ {_FORK_ROUTE}{_PRIVATE_FAST_CHECKS_TAIL} }}}}"


def frontend_required_check_runs_on_expression() -> str:
    """``runs-on`` for frontend-tests required-check jobs (browser-label fallback)."""
    return f"${{{{ {_FORK_ROUTE}{_FRONTEND_FAST_CHECKS_TAIL} }}}}"


def pick_runner_emit_fast_checks_runner_function_bash() -> str:
    """Bash function for pick-runner; must run on every success path before ``exit 0``."""
    return f"""\
emit_fast_checks_runner() {{
  local fast_checks="${{RD_FAST_CHECKS_RUNNER:-}}"
  if [ -n "$fast_checks" ] && [ "$fast_checks" != "hosted" ] && [ "$fast_checks" != "local" ]; then
    echo "::error::{RD_FAST_CHECKS_VAR} must be 'hosted', 'local', or unset (got '$fast_checks')"
    exit 1
  fi
  if [ "$FORK_PR" = "true" ]; then
    echo "fast_checks_runner=ubuntu-latest" >> "$GITHUB_OUTPUT"
  elif [ "$REPOSITORY_PRIVATE" != "true" ] && [ "$CONFIGURED_MODE" != "local" ]; then
    echo "fast_checks_runner=ubuntu-latest" >> "$GITHUB_OUTPUT"
  elif [ "$REPOSITORY_PRIVATE" = "true" ] && [ "$EVENT_NAME" = "merge_group" ] && [ "$CI_QUEUE_RUNNER_MODE" = "hosted" ]; then
    echo "fast_checks_runner=ubuntu-latest" >> "$GITHUB_OUTPUT"
  elif [ "$REPOSITORY_PRIVATE" = "true" ] && [ "$fast_checks" = "hosted" ] && [ "$EVENT_NAME" = "merge_group" ]; then
    echo "fast_checks_runner=ubuntu-latest" >> "$GITHUB_OUTPUT"
  else
    echo "fast_checks_runner=d-sorg-fleet" >> "$GITHUB_OUTPUT"
  fi
}}"""


def pick_runner_fast_checks_bash_fragment() -> str:
    """Single invocation of ``emit_fast_checks_runner`` (legacy name for tests and docs)."""
    return "emit_fast_checks_runner"


def pick_runner_runs_on_fork_tail() -> str:
    """Expression tail for jobs that follow pick-runner fleet routing (not fast-check)."""
    return (
        "(github.event_name == 'workflow_dispatch' && inputs.force_local) && 'd-sorg-fleet' || "
        f"({_PUBLIC_HOSTED_TAIL})"
    )


def pick_runner_job_runs_on_expression() -> str:
    """``pick-runner`` job ``runs-on`` (glue stays on fleet for private repos)."""
    return (
        "${{ github.event.pull_request && "
        "github.event.pull_request.head.repo.full_name != github.repository && "
        f"'ubuntu-latest' || {pick_runner_runs_on_fork_tail()} }}"
    )


def pick_runner_fast_checks_runs_on_expression() -> str:
    """``runs-on`` for ci-standard jobs that use ``needs.pick-runner.outputs.fast_checks_runner``."""
    return (
        "${{ github.event.pull_request && "
        "github.event.pull_request.head.repo.full_name != github.repository && "
        "'ubuntu-latest' || needs.pick-runner.outputs.fast_checks_runner }}"
    )


def hosted_required_check_jobs(repo_root: Path | None = None) -> frozenset[tuple[str, str]]:
    """Postcondition: pairs are workflow file names and job ids allowed on hosted fast-check lane."""
    root = repo_root or Path(__file__).resolve().parents[1]
    policy_path = root / "config" / "required_status_checks_policy.json"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    pairs: set[tuple[str, str]] = set()
    for entry in payload["required_contexts"]:
        workflow_file = _WORKFLOW_NAME_TO_FILE.get(entry["source_workflow"])
        if not workflow_file:
            continue
        job_id = entry["job_id"]
        pairs.add((workflow_file, job_id))
        if job_id == "tests-required":
            pairs.add((workflow_file, "tests"))
    return frozenset(pairs)


Lane = Literal["default", "required_check"]


def runs_on_uses_fast_checks_lane(runs_on: str) -> bool:
    """Whether a job's runs-on expression participates in the private fast-check lane."""
    text = runs_on.replace("\n", " ")
    return RD_FAST_CHECKS_VAR in text or "fast_checks_runner" in text
