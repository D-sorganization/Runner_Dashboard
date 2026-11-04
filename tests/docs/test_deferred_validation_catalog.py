"""Deferred-validation catalog contract for Runner_Dashboard (#2511)."""

from __future__ import annotations

import json
from pathlib import Path

_CATALOG = Path(__file__).resolve().parents[2] / "docs" / "planning" / "deferred-validation.json"


def test_deferred_validation_catalog_lists_fleet_decision_live_run() -> None:
    data = json.loads(_CATALOG.read_text(encoding="utf-8"))
    ids = {row["id"] for row in data["records"]}
    assert "DV-#2511" in ids
    row = next(r for r in data["records"] if r["id"] == "DV-#2511")
    assert row["origin_issue_url"].endswith("/issues/2511")
    assert "fleet-decision" in row["acceptance_criteria"][0]
