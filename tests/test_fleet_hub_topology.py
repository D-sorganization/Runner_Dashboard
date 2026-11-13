"""Hub topology from machine_registry.yml (issue #2022)."""

from __future__ import annotations

import pytest
from fleet_autoconfig import (
    apply_fleet_identity_from_registry,
    assert_fleet_identity_no_self_loop,
    assert_unique_hub_role,
    resolve_hub_url_from_registry,
)

_REGISTRY_HUB_DESK = {
    "machines": [
        {
            "name": "hub-a",
            "role": "hub",
            "dashboard_url": "http://hub-a.fleet-example.ts.net:8321",
        },
        {
            "name": "node-a",
            "role": "node",
            "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
        },
        {
            "name": "node-c",
            "role": "node",
            "dashboard_url": "http://node-c.fleet-example.ts.net:8321",
        },
    ]
}


def test_resolve_hub_url_from_registry() -> None:
    assert (
        resolve_hub_url_from_registry(_REGISTRY_HUB_DESK)
        == "http://hub-a.fleet-example.ts.net:8321"
    )


def test_assert_unique_hub_role_rejects_dual_hub() -> None:
    dual = {
        "machines": [
            {"name": "hub-a", "role": "hub", "dashboard_url": "http://desk:8321"},
            {"name": "node-a", "role": "hub", "dashboard_url": "http://ct:8321"},
        ]
    }
    with pytest.raises(RuntimeError, match="machines declare role=hub"):
        assert_unique_hub_role(dual)


def test_apply_fleet_identity_node_derives_hub_from_registry() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        machine_role_env=None,
        display_name="node-a",
        platform_node="node-a",
        runner_aliases=None,
    )
    assert identity.machine_role == "node"
    assert identity.machine_role_source == "registry"
    assert identity.hub_url == "http://hub-a.fleet-example.ts.net:8321"
    assert identity.hub_url_source == "registry"


def test_apply_fleet_identity_hub_has_no_hub_url() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        machine_role_env=None,
        display_name="hub-a",
        platform_node="hub-a",
        runner_aliases=None,
    )
    assert identity.machine_role == "hub"
    assert identity.hub_url is None


def test_apply_fleet_identity_env_overrides_hub_url() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env="http://override.example:8321",
        machine_role_env=None,
        display_name="node-a",
        platform_node="node-a",
        runner_aliases=None,
    )
    assert identity.hub_url == "http://override.example:8321"
    assert identity.hub_url_source == "env"


def test_apply_fleet_identity_node_c_points_at_desk_hub() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        machine_role_env=None,
        display_name="node-c",
        platform_node="node-c",
        runner_aliases=None,
    )
    assert identity.machine_role == "node"
    assert identity.hub_url == "http://hub-a.fleet-example.ts.net:8321"


def test_apply_fleet_identity_env_override_machine_role() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        machine_role_env="node",
        display_name="hub-a",
        platform_node="hub-a",
        runner_aliases=None,
    )
    assert identity.machine_role == "node"
    assert identity.machine_role_source == "env"


def test_assert_fleet_identity_no_self_loop_rejects_hub_host_as_node() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env=None,
        machine_role_env="node",
        display_name="hub-a",
        platform_node="hub-a",
        runner_aliases=None,
    )
    with pytest.raises(RuntimeError, match="registered hub"):
        assert_fleet_identity_no_self_loop(
            identity,
            _REGISTRY_HUB_DESK,
            display_name="hub-a",
            platform_node="hub-a",
        )


def test_assert_fleet_identity_no_self_loop_rejects_hub_url_to_localhost() -> None:
    identity = apply_fleet_identity_from_registry(
        _REGISTRY_HUB_DESK,
        hub_url_env="http://127.0.0.1:8321",
        machine_role_env=None,
        display_name="node-a",
        platform_node="node-a",
        runner_aliases=None,
    )
    with pytest.raises(RuntimeError, match="HUB_URL points at this dashboard"):
        assert_fleet_identity_no_self_loop(
            identity,
            _REGISTRY_HUB_DESK,
            display_name="node-a",
            platform_node="node-a",
        )
