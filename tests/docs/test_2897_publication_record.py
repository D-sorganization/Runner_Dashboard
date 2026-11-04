"""Publication record for #2897 acceptance (comment bodies + F5 gate)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PUBLICATION = REPO / "docs/research/2897-publication-record.md"
FINDINGS = REPO / "docs/research/queued-job-backlog-2897-findings.md"
RUNBOOK = REPO / "docs/runbooks/queued-job-diagnostic.md"


def test_publication_record_names_top_causes_and_f5_deferral() -> None:
    text = PUBLICATION.read_text(encoding="utf-8")
    assert "label_mismatch" in text
    assert "d-sorg-fleet-public-heavy" in text
    assert "proceed: false" in text.lower() or "**proceed: false**" in text
    assert "#2768" in text


def test_findings_and_runbook_cross_link_publication() -> None:
    findings = FINDINGS.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "2897-publication-record.md" in findings
    assert "2897-publication-record.md" in runbook
    assert "queued-job-backlog-2897-findings.md" in runbook
