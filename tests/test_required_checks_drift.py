"""Regression tests for scripts/check_required_checks_drift.py (issue #1119).

These tests run entirely offline against committed fixtures:

- tests/contracts/branch_protection_snapshot.json and
  tests/contracts/rulesets_snapshot.json are a snapshot of the ACTUAL live
  branch-protection/ruleset configuration for `main`, captured 2026-08-25.
  Comparing them against config/required_status_checks_policy.json must
  reproduce the exact gap described in issue #1119 (the 'guard' context is
  not required anywhere). This is a "does the detector actually detect the
  known bug" test, not a mock.

- tests/contracts/rulesets_snapshot_fixed_example.json is an illustrative
  example of what a compliant ruleset would look like (all three policy
  contexts present), used to prove the detector reports clean when the gap
  is closed.

- The `if: always()` fails-closed check is exercised against a fixture
  workflow snippet, independent of the real ci-standard.yml content, so this
  test does not need to change if the workflow file is edited later.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_required_checks_drift as drift  # noqa: E402

_CONTRACTS = Path(__file__).parent / "contracts"
_POLICY = _ROOT / "config" / "required_status_checks_policy.json"


def _load(name: str):
    return drift._load_json(_CONTRACTS / name)


def test_policy_file_lists_the_three_intended_contexts() -> None:
    policy = drift._load_json(_POLICY)
    contexts = {entry["context"] for entry in policy["required_contexts"]}
    assert contexts == {"quality-gate", "tests", "guard"}


def test_live_snapshot_reproduces_the_known_issue_1119_gap() -> None:
    """The committed 2026-08-25 snapshot must show 'guard' as missing --
    this is the exact condition that let PR #1118 merge while the
    Anti-Phantom Merge Guard was still queued. If this test ever starts
    failing because the snapshot files were refreshed post-fix, that's
    good news: refresh rulesets_snapshot_fixed_example.json's role instead
    and update this test to assert an empty gap.
    """
    policy = drift._load_json(_POLICY)
    protection = _load("branch_protection_snapshot.json")
    rulesets = _load("rulesets_snapshot.json")

    missing = drift.required_context_drift(policy, protection, rulesets)

    assert missing == ["guard"]


def test_fixed_example_ruleset_has_no_drift() -> None:
    policy = drift._load_json(_POLICY)
    protection = _load("branch_protection_snapshot.json")
    rulesets = _load("rulesets_snapshot_fixed_example.json")

    missing = drift.required_context_drift(policy, protection, rulesets)

    assert missing == []


def test_required_contexts_from_snapshot_unions_protection_and_rulesets() -> None:
    protection = {"required_status_checks": {"contexts": ["quality-gate"]}}
    rulesets = [
        {
            "enforcement": "active",
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "tests"}, {"context": "guard"}]},
                }
            ],
        }
    ]

    contexts = drift.required_contexts_from_snapshot(protection, rulesets)

    assert contexts == {"quality-gate", "tests", "guard"}


_FIXTURE_WORKFLOW_NOT_FAILS_CLOSED = """
jobs:
  tests:
    runs-on: ubuntu-latest
    steps: []
  tests-required:
    name: tests
    needs: [tests]
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

_FIXTURE_WORKFLOW_FAILS_CLOSED = """
jobs:
  tests:
    runs-on: ubuntu-latest
    steps: []
  tests-required:
    name: tests
    needs: [tests]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: |
          if [ "${{ needs.tests.result }}" != "success" ]; then
            echo "tests failed or was skipped" >&2
            exit 1
          fi
"""


def test_check_job_fails_closed_flags_missing_always_guard() -> None:
    problems = drift.check_job_fails_closed(_FIXTURE_WORKFLOW_NOT_FAILS_CLOSED, "tests-required")

    assert len(problems) == 1
    assert "if: always()" in problems[0]


def test_check_job_fails_closed_accepts_always_guard() -> None:
    problems = drift.check_job_fails_closed(_FIXTURE_WORKFLOW_FAILS_CLOSED, "tests-required")

    assert problems == []


def test_check_job_fails_closed_reports_missing_job() -> None:
    problems = drift.check_job_fails_closed(_FIXTURE_WORKFLOW_FAILS_CLOSED, "does-not-exist")

    assert len(problems) == 1
    assert "not found" in problems[0]


def test_main_exits_nonzero_on_known_snapshot(capsys) -> None:
    rc = drift.main(
        [
            "--policy",
            str(_POLICY),
            "--branch-protection-snapshot",
            str(_CONTRACTS / "branch_protection_snapshot.json"),
            "--rulesets-snapshot",
            str(_CONTRACTS / "rulesets_snapshot.json"),
            "--skip-fails-closed-check",
        ]
    )

    assert rc == 1
    assert "guard" in capsys.readouterr().err


def test_actual_ci_standard_tests_required_job_is_not_yet_fail_closed() -> None:
    """Documents today's real state of ci-standard.yml: the 'tests-required'
    job (which reports the required 'tests' context) has no `if: always()`
    guard, so it is silently SKIPPED -- not FAILED -- whenever the pytest
    matrix job fails. This reproduces the PR #1116 mechanism directly
    against the live workflow file. Fixing this is a workflow-YAML change
    and is therefore left to a human per issue #1119's governance note; when
    it is fixed, this test should be updated to assert an empty list.
    """
    workflow_path = _ROOT / ".github" / "workflows" / "ci-standard.yml"
    problems = drift.check_job_fails_closed(workflow_path.read_text(encoding="utf-8"), "tests-required")

    assert len(problems) == 1
    assert "if: always()" in problems[0]


def test_main_exits_zero_on_fixed_example(capsys) -> None:
    rc = drift.main(
        [
            "--policy",
            str(_POLICY),
            "--branch-protection-snapshot",
            str(_CONTRACTS / "branch_protection_snapshot.json"),
            "--rulesets-snapshot",
            str(_CONTRACTS / "rulesets_snapshot_fixed_example.json"),
            "--skip-fails-closed-check",
        ]
    )

    assert rc == 0
    assert "matches policy" in capsys.readouterr().out
