"""Parse real GitHub fixtures into delivery-metrics models (#2118)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from delivery_metrics.config import delivery_metrics_enabled
from delivery_metrics.parse import parse_check_runs, parse_pr_snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "delivery_metrics"
REPO = "D-sorganization/Runner_Dashboard"


def test_parse_pr_from_recorded_pull() -> None:
    payload = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    pr = parse_pr_snapshot(REPO, payload)
    assert pr.number == 2111
    assert pr.merged is True
    assert pr.head_sha == payload["head"]["sha"]
    assert pr.author_login == "dieterolson"
    assert pr.changed_files == 13


def test_parse_check_runs_from_recorded_payload() -> None:
    payload = json.loads((FIXTURES / "check_runs_pr_2111.json").read_text(encoding="utf-8"))
    head_sha = payload["check_runs"][0]["head_sha"]
    runs = parse_check_runs(REPO, head_sha, payload)
    assert len(runs) == payload["total_count"]
    names = {r.name for r in runs}
    assert "lint" in names
    assert all(r.repo_full == REPO for r in runs)
    lint = next(r for r in runs if r.name == "lint")
    assert lint.conclusion == "success"
    assert lint.app_slug == "github-actions"


def test_delivery_metrics_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_ENABLE_DELIVERY_METRICS", raising=False)
    assert delivery_metrics_enabled() is False
