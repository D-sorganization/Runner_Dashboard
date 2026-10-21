"""Frozen measurement constants for RM-CI-000 (issue #2216)."""

from __future__ import annotations

# Strict 14-day UTC window from ci-throughput-plan Appendix A.
WINDOW_START_UTC = "2026-10-07T00:00:00Z"
WINDOW_END_UTC = "2026-10-21T23:59:59Z"
SNAPSHOT_DATE = "2026-10-21"

ORG = "D-sorganization"

FLEET_CI_REPOS: tuple[str, ...] = (
    f"{ORG}/UpstreamDrift",
    f"{ORG}/AffineDrift",
    f"{ORG}/Tools",
    f"{ORG}/Repository_Management",
    f"{ORG}/Runner_Dashboard",
)

GASIFICATION_MODEL_REPO = f"{ORG}/Gasification_Model"

WORKFLOW_EVENTS: tuple[str, ...] = (
    "pull_request",
    "merge_group",
    "push",
    "schedule",
)

CI_STANDARD_WORKFLOW_NAME = "CI Standard"

GITHUB_LIST_CAP = 1000
MIN_JOB_SAMPLE_RUNS = 50

FAILURE_DENOMINATOR_CONCLUSIONS: frozenset[str] = frozenset(
    {"success", "failure", "cancelled", "timed_out"}
)

NEEDS_CREATED_AT_NOTE = (
    "GitHub sets job created_at when the job record is created; for jobs with "
    "needs:, created_at may precede dependency completion, so queue_sec "
    "(started_at - created_at) can include waiting on needs: as well as runner "
    "queue time."
)

REQUIRED_CHECKS_PATH = "docs/operations/ci-required-checks-live.json"

SCHEMA_VERSION = 1
