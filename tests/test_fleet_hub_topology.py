"""Hub topology from machine_registry.yml (issue #2022)."""

from __future__ import annotations

import pytest
from fleet_autoconfig import (
    apply_fleet_identity_from_registry,
    assert_unique_hub_role,
    resolve_hub_url_from_registry,
)

_REGISTRY_HUB_DESK = {
    "machines": [
        {
            "name": "DeskComputer",
            "role": "hub",
            "dashboard_url": "http://deskcomputer.tail2bbcc7.ts.net:8321",
        },
        {
            "name": "ControlTower",
            "role": "node",
            "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
        },
        {
            "name": "Brick",
            "role": "node",
            "dashboard_url": "http://brick.tail2bbcc7.ts.net:8321",
        },
    ]
}


def test_resolve_hub_url_from_registry() -> None:
    assert (
        resolve_hub_url_from_registry(_REGISTRY_HUB_DESK)
        == "http://deskcomputer.tail2bbcc7.ts.net:8321"
    )


def test_assert_unique_hub_role_rejects_dual_hub() -> None:
    dual = {
        "machines": [
            {"name": "DeskComputer", "role": "hub", "dashboard_url": "http://desk:8321"},
            {"name": "ControlTower", "role": "hub", "dashboard_url": "http://ct:8321"},
        ]
    }
    with pytest.raises(RuntimeError, match="machines declare role=hub"):
        assert_unique_hub_role(dual)


def test_apply_fleet_identity_node_derives_hub_from_registry() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        machine_role_env=None,
        display_name="ControlTower",
        platform_node="controltower",
        runner_aliases=None,
    )
    assert identity.machine_role == "node"
    assert identity.machine_role_source == "registry"
    assert identity.hub_url == "http://deskcomputer.tail2bbcc7.ts.net:8321"
    assert identity.hub_url_source == "registry"


def test_apply_fleet_identity_hub_has_no_hub_url() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        machine_role_env=None,
        display_name="DeskComputer",
        platform_node="deskcomputer",
        runner_aliases=None,
    )
    assert identity.machine_role == "hub"
    assert identity.hub_url is None


def test_apply_fleet_identity_env_overrides_hub_url() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env="http://override.example:8321",
        machine_role_env=None,
        display_name="ControlTower",
        platform_node="controltower",
        runner_aliases=None,
    )
    assert identity.hub_url == "http://override.example:8321"
    assert identity.hub_url_source == "env"
