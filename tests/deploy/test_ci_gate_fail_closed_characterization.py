"""Fail-closed CI gate semantics (Refs D-sorganization/Repository_Management#2883)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.check_runs import (  # noqa: E402
    SKIPPED_CONCLUSION_ALLOWLIST,
    check_name_passes,
)
from node_auto_deploy.ci_gate import ci_gate_for_commit  # noqa: E402
from node_auto_deploy.required_contexts import (  # noqa: E402
    RequiredContextsPolicyError,
    load_required_deploy_contexts,
)

_SHA = "a" * 40
_SUCCESS_RUN = {
    "head_sha": _SHA,
    "name": "quality-gate",
    "status": "completed",
    "conclusion": "success",
}


def test_skipped_alone_does_not_satisfy_check_name() -> None:
    assert not check_name_passes(
        [{"status": "completed", "conclusion": "skipped", "name": "tests"}],
    )


def test_duplicate_skipped_and_success_satisfies() -> None:
    # #4419: a skipped sibling (merge-queue or path-filtered run) carries no verdict.
    assert check_name_passes(
        [
            {"status": "completed", "conclusion": "skipped", "name": "lint"},
            {"status": "completed", "conclusion": "success", "name": "lint"},
        ],
    )


def test_cancelled_superseded_by_success_still_satisfies() -> None:
    assert check_name_passes(
        [
            {"status": "completed", "conclusion": "cancelled", "name": "docker-build-scan"},
            {"status": "completed", "conclusion": "success", "name": "docker-build-scan"},
        ],
    )


def test_ci_gate_rejects_skipped_only_required_context() -> None:
    ok, detail = ci_gate_for_commit(
        [{"head_sha": _SHA, "name": "tests", "status": "completed", "conclusion": "skipped"}],
        _SHA,
        required_contexts=("tests",),
    )
    assert ok is False
    assert "tests" in detail


def test_ci_gate_accepts_skipped_only_optional_check() -> None:
    # #4425: a job that skipped by design is not a failure unless it is a required context.
    ok, detail = ci_gate_for_commit(
        [{"head_sha": _SHA, "name": "guard", "status": "completed", "conclusion": "skipped"}],
        _SHA,
        required_contexts=(),
    )
    assert ok is True, detail


def test_ci_gate_accepts_duplicate_skipped_and_success() -> None:
    ok, detail = ci_gate_for_commit(
        [
            {"head_sha": _SHA, "name": "tests", "status": "completed", "conclusion": "skipped"},
            {"head_sha": _SHA, "name": "tests", "status": "completed", "conclusion": "success"},
        ],
        _SHA,
        required_contexts=("tests",),
    )
    assert ok is True, detail


def test_missing_required_context_fails_closed() -> None:
    ok, detail = ci_gate_for_commit(
        [
            {"head_sha": _SHA, "name": "quality-gate", "status": "completed", "conclusion": "success"},
        ],
        _SHA,
    )
    assert ok is False
    assert "required checks missing" in detail


def test_skip_allowlist_holds_only_the_pull_request_guard() -> None:
    """The one required context that skips by design on a main commit (#4419)."""
    assert SKIPPED_CONCLUSION_ALLOWLIST == frozenset({"guard"})

    workflow = (REPO / ".github" / "workflows" / "anti-phantom-merge.yml").read_text(encoding="utf-8")
    job = workflow.split("\n  guard:\n", 1)[1]
    condition = job.split("    if: >-\n", 1)[1].split("\n    steps:", 1)[0]
    assert "\n  merge_group:" in workflow, "guard must still report (as skipped) in the merge queue"
    assert "github.event.pull_request.base.ref" in condition, "guard must still run only for a pull request"
    assert "merge_group" not in condition


def test_allowlisted_guard_passes_when_skipped_but_not_when_cancelled_or_failed() -> None:
    def gate(*conclusions: str) -> bool:
        runs = [
            {"id": index, "head_sha": _SHA, "name": "guard", "status": "completed", "conclusion": conclusion}
            for index, conclusion in enumerate(conclusions, start=1)
        ]
        return ci_gate_for_commit(runs, _SHA, required_contexts=("guard",))[0]

    assert gate("skipped") is True
    assert gate("skipped", "skipped") is True
    assert gate("neutral") is False
    assert gate("cancelled") is False
    assert gate("skipped", "cancelled") is False
    assert gate("success", "failure") is False
    assert gate("failure", "skipped") is False


def test_ci_gate_fails_when_required_policy_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no_policy.json"
    with _policy_path(missing):
        ok, detail = ci_gate_for_commit([_SUCCESS_RUN], _SHA)
    assert ok is False
    assert "required checks policy error" in detail
    assert "cannot read policy" in detail


def test_ci_gate_fails_when_required_policy_malformed(tmp_path: Path) -> None:
    bad = tmp_path / "bad_policy.json"
    bad.write_text("not json", encoding="utf-8")
    with _policy_path(bad):
        load_required_deploy_contexts.cache_clear()
        ok, detail = ci_gate_for_commit([_SUCCESS_RUN], _SHA)
    assert ok is False
    assert "required checks policy error" in detail
    assert "invalid JSON" in detail


def test_load_required_deploy_contexts_rejects_missing_file(tmp_path: Path) -> None:
    load_required_deploy_contexts.cache_clear()
    with pytest.raises(RequiredContextsPolicyError, match="cannot read policy"):
        load_required_deploy_contexts(str(tmp_path / "missing.json"))


def test_load_required_deploy_contexts_rejects_malformed_policy(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text('{"required_contexts": "not-a-list"}', encoding="utf-8")
    load_required_deploy_contexts.cache_clear()
    with pytest.raises(RequiredContextsPolicyError, match="required_contexts must be a list"):
        load_required_deploy_contexts(str(path))


class _policy_path:
    """Point load_required_deploy_contexts at a temporary policy file for one gate evaluation."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._prior_default: Path | None = None

    def __enter__(self) -> None:
        import node_auto_deploy.required_contexts as mod  # noqa: WPS433

        self._prior_default = mod._DEFAULT_POLICY
        mod._DEFAULT_POLICY = self._path
        load_required_deploy_contexts.cache_clear()

    def __exit__(self, *args: object) -> None:
        import node_auto_deploy.required_contexts as mod  # noqa: WPS433

        if self._prior_default is not None:
            mod._DEFAULT_POLICY = self._prior_default
        load_required_deploy_contexts.cache_clear()
