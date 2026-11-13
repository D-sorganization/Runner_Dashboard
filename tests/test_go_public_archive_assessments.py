"""Go-public guards for archived private-repo assessments (issue #3305)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE_ASSESSMENTS = REPO / "docs" / "archive" / "root-cleanup-2026-10-17" / "assessments"

_PRIVATE_REPO_SLUGS = (
    "Bitnet_Launcher",
    "Drake_Models",
    "Games",
    "MEB_Conversion",
    "MLProjects",
    "Maxwell-Daemon",
    "Playground",
    "QuatEngine",
    "Tools_Private",
    "UpstreamDrift",
    "Worksheet-Workshop",
    "controls",
)


def test_archive_has_no_private_repo_assessment_artifacts() -> None:
    if not ARCHIVE_ASSESSMENTS.is_dir():
        return
    names = {path.name for path in ARCHIVE_ASSESSMENTS.iterdir()}
    for slug in _PRIVATE_REPO_SLUGS:
        assert not any(slug in name for name in names), f"private assessment artifact still present for {slug}"


def test_archive_has_no_comprehensive_fleet_assessment_bundle() -> None:
    if not ARCHIVE_ASSESSMENTS.is_dir():
        return
    names = {path.name for path in ARCHIVE_ASSESSMENTS.iterdir()}
    assert "2026-04-26-comprehensive-assessment.json" not in names
    assert "2026-04-26-comprehensive-assessment.md" not in names
    assert "batch_assess.py" not in names
    assert "distribute_assessments.py" not in names
