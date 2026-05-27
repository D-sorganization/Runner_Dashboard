"""Tests for deploy/runner-cleanup.sh — issue #653 race-tolerance contract.

Issue #653 observed that ``runner-cleanup.sh`` ran under ``set -Eeuo pipefail``
and called ``systemctl stop`` without tolerating a non-zero exit code. When the
autoscaler raced with a job pickup on runner-1 and ``systemctl`` returned 1 (the
"Job for ... canceled" race), ``set -e`` aborted the loop and runners 2..N were
never touched.

The fix (landed in PR #660) wraps the stop call with ``|| stop_rc=$?`` and skips
that runner rather than aborting the loop. These tests pin the contract so
regressions are caught before they ship to the host.

All tests are static analysis of the shell script — they do not invoke bash and
do not require a Linux host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CLEANUP_SCRIPT = Path(__file__).resolve().parent.parent.parent / "deploy" / "runner-cleanup.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    """Return the full text of runner-cleanup.sh."""
    assert CLEANUP_SCRIPT.is_file(), f"deploy/runner-cleanup.sh not found at {CLEANUP_SCRIPT}"
    return CLEANUP_SCRIPT.read_text(encoding="utf-8")


class TestCleanupScriptExists:
    def test_script_present(self) -> None:
        assert CLEANUP_SCRIPT.is_file(), "deploy/runner-cleanup.sh must exist"

    def test_has_bash_shebang(self, script_text: str) -> None:
        first_line = script_text.splitlines()[0]
        assert "bash" in first_line, "Script must have a #!/usr/bin/env bash shebang"


class TestRaceTolerantStop:
    """Issue #653: systemctl stop must not abort the loop on non-zero exit."""

    def test_stop_tolerates_failure_via_or_assignment(self, script_text: str) -> None:
        """The stop call must use ``|| stop_rc=$?`` to avoid tripping ``set -e``.

        ``systemctl stop`` returns exit 1 when a job races with the stop and
        systemd cancels the stop job (log: "Job for ... canceled"). Under
        ``set -Eeuo pipefail``, an unguarded non-zero exit immediately aborts
        the script — skipping all remaining runners.

        The correct pattern is:
            run systemctl stop "$unit" || stop_rc=$?

        This captures the failure and lets the loop continue, logging the skip
        and moving on to runner-2 through runner-N.
        """
        # Both patterns that correctly tolerate failure are acceptable.
        has_or_capture = "|| stop_rc=$?" in script_text
        has_or_true = "|| true" in script_text  # weaker but acceptable
        assert has_or_capture or has_or_true, (
            "runner-cleanup.sh: systemctl stop must tolerate non-zero exit "
            "(use '|| stop_rc=$?' or '|| true') to avoid aborting the loop "
            "when a job races with the stop. See issue #653."
        )

    def test_stop_failure_is_handled_not_ignored(self, script_text: str) -> None:
        """The stop failure path must log and skip the runner, not silently continue."""
        # After the tolerant stop, the script must check stop_rc and log the skip.
        has_stop_rc_check = "stop_rc" in script_text
        assert has_stop_rc_check, (
            "runner-cleanup.sh must inspect stop_rc after the tolerant stop call "
            "to log the race and skip that runner. A bare '|| true' without "
            "any follow-up check is insufficient."
        )

    def test_loop_continues_after_stop_failure(self, script_text: str) -> None:
        """The cleanup loop must use ``continue`` to skip the failed runner.

        When ``systemctl stop`` fails the script must NOT call ``cleanup_runner_*``
        on that runner (the unit may still be active with a live job). The correct
        guard is a ``continue`` statement inside the non-zero stop_rc branch.
        """
        # Find the block that handles stop_rc != 0 and verify 'continue' appears
        # before the next call to cleanup_runner_workdir / cleanup_runner_diag.
        # We locate the stop_rc check, then scan forward for 'continue' before
        # any 'cleanup_runner_' call.
        lines = script_text.splitlines()
        stop_rc_check_idx = next(
            (i for i, line in enumerate(lines) if "stop_rc" in line and "!=" in line),
            None,
        )
        assert stop_rc_check_idx is not None, "runner-cleanup.sh must check stop_rc after the tolerant stop call"
        # From the stop_rc check, look forward for 'continue' before the next
        # call to the per-runner cleanup helpers.
        found_continue = False
        for line in lines[stop_rc_check_idx:]:
            stripped = line.strip()
            if stripped == "continue":
                found_continue = True
                break
            if "cleanup_runner_workdir" in stripped or "cleanup_runner_diag" in stripped:
                break  # Found a cleanup call before continue — fail
        assert found_continue, (
            "runner-cleanup.sh: the stop_rc failure branch must include 'continue' "
            "so the loop advances to the next runner. Without it, cleanup_runner_workdir "
            "and cleanup_runner_diag would still run on an active (busy) runner unit."
        )

    def test_stop_failure_increments_metric(self, script_text: str) -> None:
        """Each stop failure must increment CLEANUP_STOP_FAILURES for Prometheus.

        The textfile-collector metric ``runner_cleanup_stop_failures_total``
        is the ops signal for this race. If the counter is not incremented,
        silent failures in the night won't show up in dashboards.
        """
        assert "CLEANUP_STOP_FAILURES" in script_text, (
            "runner-cleanup.sh must maintain a CLEANUP_STOP_FAILURES counter "
            "that is incremented when systemctl stop fails. "
            "This counter feeds runner_cleanup_stop_failures_total in Prometheus."
        )
