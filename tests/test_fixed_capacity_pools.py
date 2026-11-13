"""Fixed always-on runner pools (``capacity_mode: fixed``) have no dashboard.

They run in a separate distro with no dashboard or autoscaler (e.g. a second
WSL distro on a host's NVMe), so the registry must accept them without a
``dashboard_url`` and must never treat them as dashboard peers.
"""

from __future__ import annotations

import pytest
from fleet_autoconfig import (
    assert_valid_active_registry,
    derive_pool_topology,
    is_fixed_capacity_pool,
)


def _registry(fixed_pool: dict) -> dict:
    return {
        "machines": [
            {
                "name": "node-a",
                "aliases": ["node-a"],
                "dashboard_url": "http://100.64.0.10:8321",
                "runner_pools": [
                    {
                        "name": "node-a-Runner",
                        "aliases": ["node-a-runner"],
                        "dashboard_url": "http://100.64.0.10:8321",
                    },
                    fixed_pool,
                ],
            }
        ]
    }


_FIXED = {"name": "node-a-CNVMe", "aliases": ["ct-nvme"], "capacity_mode": "fixed"}


@pytest.mark.parametrize(
    ("pool", "expected"),
    [
        ({"capacity_mode": "fixed"}, True),
        ({"capacity_mode": "FIXED"}, True),
        ({"capacity_mode": "autoscaled"}, False),
        ({}, False),
        ({"capacity_mode": "fixed", "retired": True}, False),
    ],
)
def test_is_fixed_capacity_pool(pool: dict, expected: bool) -> None:
    assert is_fixed_capacity_pool(pool) is expected


def test_validation_accepts_fixed_pool_without_dashboard_url() -> None:
    assert_valid_active_registry(_registry(dict(_FIXED)))


def test_validation_still_rejects_autoscaled_pool_without_dashboard_url() -> None:
    with pytest.raises(RuntimeError, match="missing dashboard_url"):
        assert_valid_active_registry(_registry({"name": "node-a-Other", "dashboard_url": ""}))


def test_validation_still_checks_fixed_pool_url_when_given() -> None:
    bad = dict(_FIXED, dashboard_url="not a url")
    with pytest.raises(RuntimeError, match="invalid dashboard_url"):
        assert_valid_active_registry(_registry(bad))


def test_topology_never_uses_fixed_pool_as_local_or_peer() -> None:
    local, peers = derive_pool_topology(
        _registry(dict(_FIXED)),
        local_port=8321,
        display_name="node-a-runner",
        platform_node="node-a",
    )
    assert local == "node-a-Runner"
    assert peers == []


def test_topology_single_dashboard_pool_plus_fixed_pool_resolves_local() -> None:
    local, _ = derive_pool_topology(
        _registry(dict(_FIXED)), local_port=8321, display_name="node-a", platform_node="node-a"
    )
    assert local == "node-a-Runner"
