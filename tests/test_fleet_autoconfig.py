from __future__ import annotations

from fleet_autoconfig import (
    current_identity_names,
    derive_fleet_nodes_from_registry,
    registry_machine_identity_names,
)


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
        "OGLaptop": "http://100.125.64.108:8321",
    }
