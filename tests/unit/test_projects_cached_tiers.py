"""Unit tests for cached_repos_in_tiers in projects.service."""

from __future__ import annotations

import pytest
from cache_utils import cache_clear, cache_set
from projects.priorities import ProjectPriority
from projects.service import cached_repos_in_tiers


def test_cached_repos_in_tiers_matching() -> None:
    """Return repos whose tier matches the requested tiers."""
    cache_set(
        "projects:priorities",
        (
            {"A": ProjectPriority(tier="P0"), "B": ProjectPriority(tier="P2")},
            None,
        ),
    )
    result = cached_repos_in_tiers(("P0", "P1"))
    assert result == frozenset({"A"})


def test_cached_repos_in_tiers_empty_cache() -> None:
    """Return None when nothing is cached."""
    cache_clear()
    assert cached_repos_in_tiers(("P0", "P1")) is None


def test_cached_repos_in_tiers_cached_error() -> None:
    """Return None when cache holds an error tuple."""
    cache_set("projects:priorities", ({}, "priority file not found"))
    assert cached_repos_in_tiers(("P0", "P1")) is None


def test_cached_repos_in_tiers_empty_dict() -> None:
    """Return None when parsed dict is empty."""
    cache_set("projects:priorities", ({}, None))
    assert cached_repos_in_tiers(("P0", "P1")) is None


def test_cached_repos_in_tiers_invalid_tier() -> None:
    """Raise ValueError when any tier is invalid."""
    with pytest.raises(ValueError, match="invalid tier 'P9'"):
        cached_repos_in_tiers(("P9",))


def test_cached_repos_in_tiers_empty_tiers() -> None:
    """Raise ValueError when tiers sequence is empty."""
    with pytest.raises(ValueError, match="tiers must be non-empty"):
        cached_repos_in_tiers(())
