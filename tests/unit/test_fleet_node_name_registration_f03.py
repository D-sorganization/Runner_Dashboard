"""WP19 F-03 (#4274): node names with '-', '_' or space must resolve to their registry entry."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from machine_registry import canonical_node_token
from staff import fleet_run_token_service as svc


def _registry(*machines: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "machines": list(machines)}


_FLEET = _registry(
    {"name": "ControlTower", "role": "hub", "aliases": ["ct"]},
    {"name": "Brick", "role": "node", "aliases": ["d-sorg-local-Brick-1"]},
    {"name": "OG Laptop", "role": "node", "aliases": ["og_laptop"]},
    {
        "name": "DeskComputer",
        "role": "node",
        "runner_pools": [{"name": "DeskComputer-Runner", "role": "runner_pool"}],
    },
    {"name": "Pi5", "role": "node", "retired": True},
)


def _resolve(monkeypatch: pytest.MonkeyPatch, registry: dict[str, Any], header: str) -> str:
    monkeypatch.setattr(svc, "load_machine_registry", lambda: registry)
    return svc.assert_registered_fleet_node(header)


@pytest.mark.parametrize(
    ("header", "canonical"),
    [
        ("Brick", "Brick"),  # plain alphanumeric control: passed before the fix
        ("d-sorg-local-Brick-1", "Brick"),
        ("OG Laptop", "OG Laptop"),
        ("og_laptop", "OG Laptop"),
        ("OG-Laptop", "OG Laptop"),
        ("DeskComputer-Runner", "DeskComputer-Runner"),
        ("  Brick  ", "Brick"),
    ],
)
def test_registered_node_names_with_separators_resolve(
    monkeypatch: pytest.MonkeyPatch, header: str, canonical: str
) -> None:
    assert _resolve(monkeypatch, _FLEET, header) == canonical


@pytest.mark.parametrize("header", ["Brick-2", "Bric", "Brickx", "local-Brick", "unknown_node", "Pi5"])
def test_unknown_or_retired_names_stay_unregistered(monkeypatch: pytest.MonkeyPatch, header: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, _FLEET, header)
    assert exc.value.status_code == 403
    assert exc.value.detail == "unregistered fleet node"


@pytest.mark.parametrize(
    "header",
    [
        "",
        "   ",
        "Bri\nck",
        "Bri\x00ck",
        "Br\tick",
        "../Brick",
        "Brick/1",
        "Bri\\ck",
        "-Brick",
        "Brick_",
        "---",
        "B" * 65,
        "Brïck",
    ],
)
def test_malformed_names_are_rejected(monkeypatch: pytest.MonkeyPatch, header: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, _FLEET, header)
    assert exc.value.status_code == 403


def test_hub_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, _FLEET, "ControlTower")
    assert exc.value.detail == "hub cannot request spoke run tokens"


@pytest.mark.parametrize("header", ["node-ab", "node_a_b", "nodeab", "node a-b"])
def test_colliding_registrations_are_never_merged(monkeypatch: pytest.MonkeyPatch, header: str) -> None:
    """Two machines whose names share one canonical token must not answer for each other."""
    registry = _registry({"name": "node-ab", "role": "node"}, {"name": "node_a_b", "role": "node"})
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, registry, header)
    assert exc.value.status_code == 403
    assert exc.value.detail == "ambiguous fleet node name"


def test_machine_and_its_own_pool_sharing_a_name_is_not_a_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry({"name": "node-a", "role": "node", "runner_pools": [{"name": "node-a"}]})
    assert _resolve(monkeypatch, registry, "node_a") == "node-a"


def test_canonical_node_token_contract() -> None:
    assert canonical_node_token("ControlTower-Runner") == "controltowerrunner"
    assert canonical_node_token(" OG_Laptop ") == canonical_node_token("og laptop") == "oglaptop"
    for bad in ("", "a/b", "a\x1fb", "a.b", "-a"):
        with pytest.raises(ValueError, match="node name"):
            canonical_node_token(bad)
