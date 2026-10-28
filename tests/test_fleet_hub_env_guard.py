"""Fail-closed guard for stale host-local HUB_URL overrides (#2058)."""

from __future__ import annotations

import pytest
from fleet_hub_env_guard import assert_hub_url_env_matches_registry

from tests.test_fleet_hub_topology import _REGISTRY_HUB_DESK


def test_stale_controltower_hub_url_fails_without_override() -> None:
    with pytest.raises(RuntimeError, match="disagrees with machine_registry"):
        assert_hub_url_env_matches_registry(
            _REGISTRY_HUB_DESK,
            hub_url_env="http://controltower.tail2bbcc7.ts.net:8321",
            allow_registry_override=False,
        )


def test_matching_hub_url_env_is_allowed() -> None:
    assert_hub_url_env_matches_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env="http://deskcomputer.tail2bbcc7.ts.net:8321",
        allow_registry_override=False,
    )


def test_empty_hub_url_env_is_allowed() -> None:
    assert_hub_url_env_matches_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        allow_registry_override=False,
    )


def test_stale_hub_url_allowed_with_override_flag() -> None:
    assert_hub_url_env_matches_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env="http://controltower.tail2bbcc7.ts.net:8321",
        allow_registry_override=True,
    )
