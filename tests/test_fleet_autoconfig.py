from __future__ import annotations

import pytest
from fleet_autoconfig import (
    assert_no_maxwell_port_collision,
    current_identity_names,
    derive_fleet_nodes_from_registry,
    derive_pool_topology,
    registry_dashboard_url_ports,
    registry_machine_identity_names,
)

_SPLIT_POOL_REGISTRY = {
    "machines": [
        {
            "name": "ControlTower",
            "aliases": ["controltower"],
            "dashboard_url": "http://ct:8321",
            "runner_pools": [
                {
                    "name": "ControlTower-NVMe",
                    "aliases": ["controltower-nvme"],
                    "dashboard_url": "http://ct:8321",
                },
                {
                    "name": "ControlTower-SSD",
                    "aliases": ["controltower-ssd"],
                    "dashboard_url": "http://ct:8322",
                },
            ],
        },
        {"name": "DeskComputer", "dashboard_url": "http://desk:8321"},
    ]
}


def test_current_identity_names_includes_display_host_and_aliases() -> None:
    identities = current_identity_names(
        display_name="ControlTower-NVMe",
        platform_node="ControlTower",
        runner_aliases="controltower-nvme, control-tower-nvme",
    )

    assert identities == {
        "controltower-nvme",
        "controltower",
        "control-tower-nvme",
    }


def test_registry_machine_identity_names_includes_runner_pools() -> None:
    machine = {
        "name": "ControlTower",
        "aliases": ["controltower"],
        "runner_pools": [
            {
                "name": "ControlTower-NVMe",
                "aliases": ["controltower-fast"],
            }
        ],
    }

    assert registry_machine_identity_names(machine) == {
        "controltower",
        "controltower-nvme",
        "controltower-fast",
    }


def test_derive_fleet_nodes_skips_parent_when_current_dashboard_is_runner_pool() -> None:
    registry = {
        "machines": [
            {
                "name": "ControlTower",
                "aliases": ["controltower"],
                "dashboard_url": "http://100.95.177.68:8321",
                "runner_pools": [
                    {
                        "name": "ControlTower-NVMe",
                        "aliases": ["controltower-nvme"],
                        "dashboard_url": "http://100.95.177.68:8321",
                    },
                    {
                        "name": "ControlTower-SSD",
                        "aliases": ["controltower-ssd"],
                        "dashboard_url": "http://100.95.177.68:8321",
                    },
                ],
            },
            {
                "name": "DeskComputer",
                "dashboard_url": "http://100.122.254.109:8321",
            },
            {
                "name": "OGLaptop",
                "dashboard_url": "http://oglaptop.tail2bbcc7.ts.net:8321",
                "tailscale_nodes": [{"ip": "100.125.64.108"}],
            },
        ],
    }

    nodes = derive_fleet_nodes_from_registry(
        registry,
        display_name="ControlTower-NVMe",
        platform_node="ControlTower",
        runner_aliases="controltower-nvme",
    )

    assert nodes == {
        "DeskComputer": "http://100.122.254.109:8321",
        "OGLaptop": "http://oglaptop.tail2bbcc7.ts.net:8321",
    }


# ---------------------------------------------------------------------------
# Issue #942: registry-derived pool topology (replaces hardcoded PORT==8322)
# ---------------------------------------------------------------------------


def test_derive_pool_topology_matches_local_pool_by_identity() -> None:
    local, peers = derive_pool_topology(
        _SPLIT_POOL_REGISTRY,
        local_port=8321,
        display_name="ControlTower-NVMe",
        runner_aliases="controltower-nvme",
    )
    assert local == "ControlTower-NVMe"
    assert peers == [{"name": "ControlTower-SSD", "url": "http://ct:8322", "port": 8322}]


def test_derive_pool_topology_falls_back_to_local_port_when_identity_ambiguous() -> None:
    # Identity only matches the physical machine, not a specific pool; the port
    # disambiguates which pool is local (the SSD pool on :8322).
    local, peers = derive_pool_topology(
        _SPLIT_POOL_REGISTRY,
        local_port=8322,
        display_name="ControlTower",
        runner_aliases="controltower",
    )
    assert local == "ControlTower-SSD"
    assert peers == [{"name": "ControlTower-NVMe", "url": "http://ct:8321", "port": 8321}]


def test_derive_pool_topology_single_pool_machine_has_no_phantom_peer() -> None:
    # A non-split machine (DeskComputer) owns no pools → no peer probing, so no
    # phantom offline ControlTower-HDD node is ever emitted (the #942 bug).
    local, peers = derive_pool_topology(
        _SPLIT_POOL_REGISTRY,
        local_port=8321,
        display_name="DeskComputer",
        platform_node="",
    )
    assert local is None
    assert peers == []


def test_registry_dashboard_url_ports_collects_machine_and_pool_ports() -> None:
    assert registry_dashboard_url_ports(_SPLIT_POOL_REGISTRY) == {8321, 8322}


def test_assert_no_maxwell_port_collision_raises_on_peer_port() -> None:
    with pytest.raises(RuntimeError, match="MAXWELL_PORT=8322"):
        assert_no_maxwell_port_collision(
            _SPLIT_POOL_REGISTRY,
            maxwell_port=8322,
            local_port=8321,
        )


def test_assert_no_maxwell_port_collision_allows_local_port_and_distinct_port() -> None:
    # Maxwell on its real default (8080) never collides.
    assert_no_maxwell_port_collision(_SPLIT_POOL_REGISTRY, maxwell_port=8080, local_port=8321)
    # Maxwell sharing *this* dashboard's own port is the co-located reverse-proxy
    # case, not a peer-pool collision, so it is allowed here.
    assert_no_maxwell_port_collision(_SPLIT_POOL_REGISTRY, maxwell_port=8321, local_port=8321)


# ---------------------------------------------------------------------------
# Issue #1169: Active registry validation and retired pool topology handling
# ---------------------------------------------------------------------------


def test_derive_pool_topology_ignores_retired_pools() -> None:
    registry = {
        "machines": [
            {
                "name": "ControlTower",
                "aliases": ["controltower"],
                "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                "runner_pools": [
                    {
                        "name": "ControlTower-NVMe",
                        "aliases": ["controltower-nvme"],
                        "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                        "retired": True,
                    },
                    {
                        "name": "ControlTower-Runner",
                        "aliases": ["controltower-runner"],
                        "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                    },
                ],
            },
        ],
    }

    local, peers = derive_pool_topology(
        registry,
        local_port=8321,
        display_name="ControlTower",
        platform_node="controltower",
    )
    assert local == "ControlTower-Runner"
    assert peers == []


def test_derive_fleet_nodes_skips_retired_machines() -> None:
    registry = {
        "machines": [
            {
                "name": "ControlTower",
                "aliases": ["controltower"],
                "dashboard_url": "http://ct:8321",
            },
            {
                "name": "brick",
                "dashboard_url": "http://brick:8321",
                "retired": True,
            },
            {
                "name": "OGLaptop",
                "dashboard_url": "http://oglaptop:8321",
            },
        ],
    }

    nodes = derive_fleet_nodes_from_registry(
        registry,
        display_name="ControlTower",
        platform_node="controltower",
    )
    assert nodes == {"OGLaptop": "http://oglaptop:8321"}
    assert "brick" not in nodes


def test_assert_valid_active_registry_passes_on_valid_registry() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    valid_registry = {
        "machines": [
            {
                "name": "ControlTower",
                "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                "runner_pools": [
                    {
                        "name": "ControlTower-NVMe",
                        "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                        "retired": True,
                    },
                    {
                        "name": "ControlTower-Runner",
                        "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                    },
                ],
            },
            {
                "name": "DeskComputer",
                "dashboard_url": "http://deskcomputer.tail2bbcc7.ts.net:8321",
            },
            {
                "name": "OGLaptop",
                "dashboard_url": "http://oglaptop.tail2bbcc7.ts.net:8321",
            },
        ]
    }
    assert_valid_active_registry(valid_registry)


def test_assert_valid_active_registry_rejects_missing_machine_name() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {"dashboard_url": "http://deskcomputer:8321"},
        ]
    }
    with pytest.raises(RuntimeError, match="Active machine missing name"):
        assert_valid_active_registry(bad_registry)


def test_assert_valid_active_registry_rejects_unresolvable_machine_url() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {"name": "DeskComputer", "dashboard_url": ""},
        ]
    }
    with pytest.raises(RuntimeError, match="no resolvable dashboard URL"):
        assert_valid_active_registry(bad_registry)


def test_assert_valid_active_registry_rejects_all_pools_retired() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {
                "name": "ControlTower",
                "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                "runner_pools": [
                    {"name": "Pool-1", "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321", "retired": True},
                ],
            }
        ]
    }
    with pytest.raises(RuntimeError, match="all are retired"):
        assert_valid_active_registry(bad_registry)


def test_assert_valid_active_registry_rejects_active_pool_without_url() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {
                "name": "ControlTower",
                "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                "runner_pools": [
                    {"name": "Pool-1", "dashboard_url": ""},
                ],
            }
        ]
    }
    with pytest.raises(RuntimeError, match="missing dashboard_url"):
        assert_valid_active_registry(bad_registry)


def test_assert_valid_active_registry_rejects_duplicate_ports_on_active_pools() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {
                "name": "ControlTower",
                "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321",
                "runner_pools": [
                    {"name": "ControlTower-NVMe", "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321"},
                    {"name": "ControlTower-Runner", "dashboard_url": "http://controltower.tail2bbcc7.ts.net:8321"},
                ],
            }
        ]
    }
    with pytest.raises(RuntimeError, match="duplicate port 8321.*retired"):
        assert_valid_active_registry(bad_registry)
