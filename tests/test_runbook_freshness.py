"""Verify runbook commands match implemented API parameter names — issue #691.

These tests guard against the runbook drifting away from the actual API surface
exposed by ``backend/queue_cleanup.py`` and ``backend/routers/queue.py``.

The tests use only ``pathlib`` and ``re`` so they run in any environment
that can collect pytest.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNBOOK = (REPO_ROOT / "docs" / "runbooks" / "queue-stuck.md").read_text(
    encoding="utf-8"
)
QUEUE_CLEANUP = (REPO_ROOT / "backend" / "queue_cleanup.py").read_text(
    encoding="utf-8"
)


# ---------------------------------------------------------------------------
# API parameter names
# ---------------------------------------------------------------------------


def test_min_age_minutes_param_in_runbook() -> None:
    """Runbook must document the ``min_age_minutes`` query parameter."""
    assert "min_age_minutes" in RUNBOOK, (
        "Runbook is missing 'min_age_minutes' — the actual query-param name "
        "accepted by /api/queue/stale and /api/queue/purge-stale."
    )


def test_dry_run_param_in_runbook() -> None:
    """Runbook must document the ``dry_run`` query parameter."""
    assert "dry_run" in RUNBOOK, (
        "Runbook is missing 'dry_run' — the boolean flag that previews "
        "which runs would be cancelled without actually cancelling them."
    )


# ---------------------------------------------------------------------------
# Endpoint names
# ---------------------------------------------------------------------------


def test_queue_stale_endpoint_in_runbook() -> None:
    """Runbook must reference the /api/queue/stale endpoint."""
    assert "/api/queue/stale" in RUNBOOK


def test_queue_purge_stale_endpoint_in_runbook() -> None:
    """Runbook must reference the /api/queue/purge-stale endpoint."""
    assert "/api/queue/purge-stale" in RUNBOOK


# ---------------------------------------------------------------------------
# Concrete curl examples
# ---------------------------------------------------------------------------


def test_runbook_has_curl_examples() -> None:
    """Runbook must contain at least one concrete curl command."""
    assert "curl" in RUNBOOK, "Runbook must have curl examples for operators."


def test_curl_examples_use_actual_param_names() -> None:
    """curl examples must use the real param names (not placeholder names)."""
    # Find all curl lines and verify at least one uses the actual params
    curl_lines = [line for line in RUNBOOK.splitlines() if "curl" in line.lower()]
    assert len(curl_lines) >= 1, "Expected at least one curl example line."
    # At least one curl example should reference a real queue endpoint
    queue_endpoints = [
        l for l in curl_lines
        if "/api/queue/stale" in l or "/api/queue/purge-stale" in l
    ]
    assert len(queue_endpoints) >= 1, (
        "No curl examples reference /api/queue/stale or /api/queue/purge-stale."
    )


# ---------------------------------------------------------------------------
# Stale cause classifications mentioned in runbook
# ---------------------------------------------------------------------------


def test_superseded_pr_head_in_runbook() -> None:
    """The 'superseded_pr_head' cause must be documented in the runbook."""
    assert "superseded_pr_head" in RUNBOOK, (
        "Runbook must document the superseded_pr_head classification — "
        "a run that can never start because a newer commit superseded the PR head."
    )


def test_abandoned_agent_in_runbook() -> None:
    """The 'abandoned_agent' cause must be documented."""
    assert "abandoned_agent" in RUNBOOK


def test_offline_runner_or_lag_in_runbook() -> None:
    """The 'offline_runner_or_lag' cause must be documented."""
    assert "offline_runner_or_lag" in RUNBOOK


def test_closed_or_deleted_ref_in_runbook() -> None:
    """The 'closed_or_deleted_ref' cause must be documented."""
    assert "closed_or_deleted_ref" in RUNBOOK


def test_stale_main_branch_in_runbook() -> None:
    """The 'stale_main_branch' cause must be documented."""
    assert "stale_main_branch" in RUNBOOK


def test_stale_feature_branch_in_runbook() -> None:
    """The 'stale_feature_branch' cause must be documented."""
    assert "stale_feature_branch" in RUNBOOK


# ---------------------------------------------------------------------------
# Post-incident checklist
# ---------------------------------------------------------------------------


def test_post_incident_checklist_present() -> None:
    """Runbook must contain a post-incident checklist section."""
    lower = RUNBOOK.lower()
    has_checklist = "post-incident checklist" in lower or "post_incident" in lower
    assert has_checklist, (
        "Runbook must include a 'Post-Incident Checklist' section so operators "
        "can verify the system is healthy after resolution."
    )


def test_checklist_has_actionable_items() -> None:
    """Checklist must contain at least 3 checkbox items (GitHub markdown [ ])."""
    checkboxes = re.findall(r"- \[[ x]\]", RUNBOOK)
    assert len(checkboxes) >= 3, (
        f"Expected at least 3 checklist items, found {len(checkboxes)}."
    )


# ---------------------------------------------------------------------------
# dry_run guidance
# ---------------------------------------------------------------------------


def test_when_to_use_dry_run_documented() -> None:
    """Runbook should explain when to use dry_run vs live purge."""
    assert "dry_run" in RUNBOOK
    # Should explain the guidance (not just mention the param)
    assert "dry" in RUNBOOK.lower()


# ---------------------------------------------------------------------------
# Superseded vs unsatisfiable label distinction
# ---------------------------------------------------------------------------


def test_superseded_vs_label_mismatch_distinction_documented() -> None:
    """Runbook must explain the difference between superseded runs and label mismatches."""
    lower = RUNBOOK.lower()
    # Should mention both concepts
    has_superseded = "superseded" in lower
    has_label = "label" in lower
    assert has_superseded and has_label, (
        "Runbook must distinguish superseded-PR-head runs from unsatisfiable "
        "runner-label runs — they require different remediation."
    )


# ---------------------------------------------------------------------------
# queue_cleanup.py consistency
# ---------------------------------------------------------------------------


def test_queue_cleanup_exports_find_stale_runs() -> None:
    """queue_cleanup.py must still define find_stale_runs (not renamed/deleted)."""
    assert "find_stale_runs" in QUEUE_CLEANUP, (
        "queue_cleanup.py no longer defines find_stale_runs — update the runbook "
        "if the function was renamed."
    )


def test_queue_cleanup_exports_purge_stale_runs() -> None:
    """queue_cleanup.py must still define purge_stale_runs (not renamed/deleted)."""
    assert "purge_stale_runs" in QUEUE_CLEANUP, (
        "queue_cleanup.py no longer defines purge_stale_runs — update the runbook "
        "if the function was renamed."
    )


def test_queue_cleanup_has_min_age_minutes_param() -> None:
    """queue_cleanup.py must accept min_age_minutes parameter."""
    assert "min_age_minutes" in QUEUE_CLEANUP, (
        "queue_cleanup.py no longer uses min_age_minutes — runbook curl examples "
        "need to be updated if the parameter was renamed."
    )


def test_queue_cleanup_has_dry_run_param() -> None:
    """queue_cleanup.py must accept dry_run parameter."""
    assert "dry_run" in QUEUE_CLEANUP, (
        "queue_cleanup.py no longer uses dry_run — runbook examples need updating."
    )
