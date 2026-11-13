from __future__ import annotations

import pytest
from fleet_autoconfig import (
    current_identity_names,
    derive_fleet_nodes_from_registry,
    derive_pool_topology,
    registry_dashboard_url_ports,
    registry_machine_identity_names,
)

_SPLIT_POOL_REGISTRY = {
    "machines": [
        {
            "name": "node-a",
            "aliases": ["node-a"],
            "dashboard_url": "http://ct:8321",
            "runner_pools": [
                {
                    "name": "node-a-NVMe",
                    "aliases": ["node-a-nvme"],
                    "dashboard_url": "http://ct:8321",
                },
                {
                    "name": "node-a-SSD",
                    "aliases": ["node-a-ssd"],
                    "dashboard_url": "http://ct:8322",
                },
            ],
        },
        {"name": "hub-a", "dashboard_url": "http://desk:8321"},
    ]
}


def test_current_identity_names_includes_display_host_and_aliases() -> None:
    identities = current_identity_names(
        display_name="node-a-NVMe",
        platform_node="node-a",
        runner_aliases="node-a-nvme, node-a-nvme",
    )

    assert identities == {
        "node-a-nvme",
        "node-a",
        "node-a-nvme",
    }


def test_registry_machine_identity_names_includes_runner_pools() -> None:
    machine = {
        "name": "node-a",
        "aliases": ["node-a"],
        "runner_pools": [
            {
                "name": "node-a-NVMe",
                "aliases": ["node-a-fast"],
            }
        ],
    }

    assert registry_machine_identity_names(machine) == {
        "node-a",
        "node-a-nvme",
        "node-a-fast",
    }


def test_derive_fleet_nodes_skips_parent_when_current_dashboard_is_runner_pool() -> None:
    registry = {
        "machines": [
            {
                "name": "node-a",
                "aliases": ["node-a"],
                "dashboard_url": "http://100.64.254.10:8321",
                "runner_pools": [
                    {
                        "name": "node-a-NVMe",
                        "aliases": ["node-a-nvme"],
                        "dashboard_url": "http://100.64.254.10:8321",
                    },
                    {
                        "name": "node-a-SSD",
                        "aliases": ["node-a-ssd"],
                        "dashboard_url": "http://100.64.254.10:8321",
                    },
                ],
            },
            {
                "name": "hub-a",
                "dashboard_url": "http://100.64.254.13:8321",
            },
            {
                "name": "node-b",
                "dashboard_url": "http://node-b.fleet-example.ts.net:8321",
                "tailscale_nodes": [{"ip": "100.64.254.12"}],
            },
        ],
    }

    nodes = derive_fleet_nodes_from_registry(
        registry,
        display_name="node-a-NVMe",
        platform_node="node-a",
        runner_aliases="node-a-nvme",
    )

    assert nodes == {
        "hub-a": "http://100.64.254.13:8321",
        "node-b": "http://node-b.fleet-example.ts.net:8321",
    }


# ---------------------------------------------------------------------------
# Issue #942: registry-derived pool topology (replaces hardcoded PORT==8322)
# ---------------------------------------------------------------------------


def test_derive_pool_topology_matches_local_pool_by_identity() -> None:
    local, peers = derive_pool_topology(
        _SPLIT_POOL_REGISTRY,
        local_port=8321,
        display_name="node-a-NVMe",
        runner_aliases="node-a-nvme",
    )
    assert local == "node-a-NVMe"
    assert peers == [{"name": "node-a-SSD", "url": "http://ct:8322", "port": 8322}]


def test_derive_pool_topology_falls_back_to_local_port_when_identity_ambiguous() -> None:
    # Identity only matches the physical machine, not a specific pool; the port
    # disambiguates which pool is local (the SSD pool on :8322).
    local, peers = derive_pool_topology(
        _SPLIT_POOL_REGISTRY,
        local_port=8322,
        display_name="node-a",
        runner_aliases="node-a",
    )
    assert local == "node-a-SSD"
    assert peers == [{"name": "node-a-NVMe", "url": "http://ct:8321", "port": 8321}]


def test_derive_pool_topology_single_pool_machine_has_no_phantom_peer() -> None:
    # A non-split machine (hub-a) owns no pools → no peer probing, so no
    # phantom offline node-a-HDD node is ever emitted (the #942 bug).
    local, peers = derive_pool_topology(
        _SPLIT_POOL_REGISTRY,
        local_port=8321,
        display_name="hub-a",
        platform_node="",
    )
    assert local is None
    assert peers == []


def test_registry_dashboard_url_ports_collects_machine_and_pool_ports() -> None:
    assert registry_dashboard_url_ports(_SPLIT_POOL_REGISTRY) == {8321, 8322}


# ---------------------------------------------------------------------------
# Issue #1169: Active registry validation and retired pool topology handling
# ---------------------------------------------------------------------------


def test_derive_pool_topology_ignores_retired_pools() -> None:
    registry = {
        "machines": [
            {
                "name": "node-a",
                "aliases": ["node-a"],
                "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                "runner_pools": [
                    {
                        "name": "node-a-NVMe",
                        "aliases": ["node-a-nvme"],
                        "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                        "retired": True,
                    },
                    {
                        "name": "node-a-Runner",
                        "aliases": ["node-a-runner"],
                        "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                    },
                ],
            },
        ],
    }

    local, peers = derive_pool_topology(
        registry,
        local_port=8321,
        display_name="node-a",
        platform_node="node-a",
    )
    assert local == "node-a-Runner"
    assert peers == []


def test_derive_fleet_nodes_skips_retired_machines() -> None:
    registry = {
        "machines": [
            {
                "name": "node-a",
                "aliases": ["node-a"],
                "dashboard_url": "http://ct:8321",
            },
            {
                "name": "node-c",
                "dashboard_url": "http://node-c:8321",
                "retired": True,
            },
            {
                "name": "node-b",
                "dashboard_url": "http://node-b:8321",
            },
        ],
    }

    nodes = derive_fleet_nodes_from_registry(
        registry,
        display_name="node-a",
        platform_node="node-a",
    )
    assert nodes == {"node-b": "http://node-b:8321"}
    assert "node-c" not in nodes


def test_assert_valid_active_registry_passes_on_valid_registry() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    valid_registry = {
        "machines": [
            {
                "name": "node-a",
                "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                "runner_pools": [
                    {
                        "name": "node-a-NVMe",
                        "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                        "retired": True,
                    },
                    {
                        "name": "node-a-Runner",
                        "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                    },
                ],
            },
            {
                "name": "hub-a",
                "dashboard_url": "http://hub-a.fleet-example.ts.net:8321",
            },
            {
                "name": "node-b",
                "dashboard_url": "http://node-b.fleet-example.ts.net:8321",
            },
        ]
    }
    assert_valid_active_registry(valid_registry)


def test_assert_valid_active_registry_rejects_missing_machine_name() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {"dashboard_url": "http://hub-a:8321"},
        ]
    }
    with pytest.raises(RuntimeError, match="Active machine missing name"):
        assert_valid_active_registry(bad_registry)


def test_assert_valid_active_registry_rejects_unresolvable_machine_url() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {"name": "hub-a", "dashboard_url": ""},
        ]
    }
    with pytest.raises(RuntimeError, match="no resolvable dashboard URL"):
        assert_valid_active_registry(bad_registry)


def test_assert_valid_active_registry_rejects_all_pools_retired() -> None:
    from fleet_autoconfig import assert_valid_active_registry

    bad_registry = {
        "machines": [
            {
                "name": "node-a",
                "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                "runner_pools": [
                    {"name": "Pool-1", "dashboard_url": "http://node-a.fleet-example.ts.net:8321", "retired": True},
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
                "name": "node-a",
                "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
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
                "name": "node-a",
                "dashboard_url": "http://node-a.fleet-example.ts.net:8321",
                "runner_pools": [
                    {"name": "node-a-NVMe", "dashboard_url": "http://node-a.fleet-example.ts.net:8321"},
                    {"name": "node-a-Runner", "dashboard_url": "http://node-a.fleet-example.ts.net:8321"},
                ],
            }
        ]
    }
    with pytest.raises(RuntimeError, match="duplicate port 8321.*retired"):
        assert_valid_active_registry(bad_registry)
