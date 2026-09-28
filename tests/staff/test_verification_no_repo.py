"""A run with no repository promised no pull request, so verification must not ask GitHub.

Barb's live dispatch test (2026-09-27) ran an ad-hoc task with no repo. The
ad-hoc role opens PRs, so verification probed ``gh`` for repo ``''`` and
recorded ``unverified: GitHub lookup failed: gh: Not Found (HTTP 404)``, which
would have turned into ``failed`` once the 24 h pending limit passed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from staff import verification
from staff.store import RunRecord


class _ExplodingProbe:
    def find(self, repo: str, branch: str) -> None:
        raise AssertionError(f"GitHub must not be asked about repo {repo!r}")


def _run(repo: str) -> RunRecord:
    return RunRecord(
        id="run-adhoc",
        role="ad-hoc",
        provider="claude",
        model="sonnet",
        machine="DeskComputer",
        repo=repo,
        target_kind="prompt",
        target_ref="",
        prompt="One-line fleet status.",
        status="succeeded",
        branch="staff/ad-hoc-task-1",
        ended_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
    )


@pytest.mark.unit
def test_run_without_repository_is_not_applicable_and_never_probes_github() -> None:
    verdict = verification.evaluate(_run(""), opens_pr=True, probe=_ExplodingProbe(), now=datetime.now(UTC))

    assert verdict.verification == "not_applicable"
    assert "no repository" in verdict.detail
