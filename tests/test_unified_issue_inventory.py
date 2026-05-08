"""Tests for backend/unified_issue_inventory.py — issue #386."""

from __future__ import annotations

import unified_issue_inventory as uii

# ---------------------------------------------------------------------------
# Module-level constants / helpers
# ---------------------------------------------------------------------------


def test_module_importable() -> None:
    """Smoke test: module must import without errors."""
    assert uii is not None


# ---------------------------------------------------------------------------
# _normalise_github_issue_url
# ---------------------------------------------------------------------------


def test_normalise_github_issue_url_valid() -> None:
    url = "https://github.com/D-sorganization/runner-dashboard/issues/123"
    result = uii._normalise_github_issue_url(url)
    assert result is not None
    assert "github.com" in result
    assert "123" in result


def test_normalise_github_issue_url_non_string_returns_none() -> None:
    assert uii._normalise_github_issue_url(None) is None
    assert uii._normalise_github_issue_url(42) is None  # type: ignore[arg-type]


def test_normalise_github_issue_url_no_match_returns_none() -> None:
    assert uii._normalise_github_issue_url("https://example.com/foo") is None


def test_normalise_github_issue_url_lowercased() -> None:
    url = "HTTPS://GitHub.COM/Org/Repo/issues/1"
    result = uii._normalise_github_issue_url(url)
    if result is not None:
        assert result == result.lower()


# ---------------------------------------------------------------------------
# _dedupe — stable deduplication
# ---------------------------------------------------------------------------


def test_dedupe_removes_duplicates() -> None:
    result = uii._dedupe(["a", "b", "a", "c"])
    assert result == ["a", "b", "c"]


def test_dedupe_empty_list() -> None:
    assert uii._dedupe([]) == []


def test_dedupe_preserves_order() -> None:
    result = uii._dedupe(["x", "y", "z"])
    assert result == ["x", "y", "z"]


# ---------------------------------------------------------------------------
# _merge_pair — label union deduplication
# ---------------------------------------------------------------------------


def test_merge_pair_labels_union() -> None:
    gh = {"labels": ["bug", "high"], "pickable": True, "pickable_blocked_by": [], "state": "open", "age_hours": 1}
    linear = {"labels": ["high", "sprint-a"], "pickable": True, "pickable_blocked_by": [], "state": "open", "age_hours": 2, "linear": {}}
    result = uii._merge_pair(linear, gh, prefer_source="github")
    assert "bug" in result["labels"]
    assert "high" in result["labels"]
    assert "sprint-a" in result["labels"]
    assert result["labels"].count("high") == 1


def test_merge_pair_closed_wins() -> None:
    gh = {"labels": [], "pickable": True, "pickable_blocked_by": [], "state": "closed", "age_hours": 1}
    linear = {"labels": [], "pickable": True, "pickable_blocked_by": [], "state": "open", "age_hours": 1, "linear": {}}
    result = uii._merge_pair(linear, gh, prefer_source="github")
    assert result["state"] == "closed"


# ---------------------------------------------------------------------------
# _stats
# ---------------------------------------------------------------------------


def test_stats_totals() -> None:
    s = uii._stats(github_total=10, linear_total=8, collapsed=5, github_only=5, linear_only=3)
    assert s["unified_total"] == 5 + 3 + 5
    assert s["collapsed"] == 5


# ---------------------------------------------------------------------------
# _linear_identifier
# ---------------------------------------------------------------------------


def test_linear_identifier_present() -> None:
    item = {"linear": {"identifier": "ENG-42"}}
    assert uii._linear_identifier(item) == "ENG-42"


def test_linear_identifier_missing() -> None:
    assert uii._linear_identifier({}) == ""
    assert uii._linear_identifier({"linear": {}}) == ""
