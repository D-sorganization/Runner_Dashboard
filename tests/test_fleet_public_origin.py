from __future__ import annotations

from fleet_public_origin import collect_hub_public_origins


def test_origin_collection_uses_fleet_resolvers_and_ignores_non_hubs() -> None:
    registry = {"machines": [{"name": "hub", "role": "hub"}, {"name": "node", "role": "node"}]}

    def machines(value):
        return value["machines"]

    def machine_url(machine):
        return "http://hub.tailnet:8321/path" if machine["role"] == "hub" else ""

    assert collect_hub_public_origins(registry, active_machines=machines, machine_url=machine_url) == (
        "https://hub.tailnet",
    )
