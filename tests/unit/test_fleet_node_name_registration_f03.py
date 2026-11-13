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
    {"name": "HubAlpha", "role": "hub", "aliases": ["ha"]},
    {"name": "Kiln", "role": "node", "aliases": ["d-sorg-local-Kiln-1"]},
    {"name": "Lab Bench", "role": "node", "aliases": ["lab_bench"]},
    {
        "name": "TowerBox",
        "role": "node",
        "runner_pools": [{"name": "TowerBox-Runner", "role": "runner_pool"}],
    },
    {"name": "OldNode7", "role": "node", "retired": True},
)


def _resolve(monkeypatch: pytest.MonkeyPatch, registry: dict[str, Any], header: str) -> str:
    monkeypatch.setattr(svc, "load_machine_registry", lambda: registry)
    return svc.assert_registered_fleet_node(header)


@pytest.mark.parametrize(
    ("header", "canonical"),
    [
        ("Kiln", "Kiln"),  # plain alphanumeric control: passed before the fix
        ("d-sorg-local-Kiln-1", "Kiln"),
        ("Lab Bench", "Lab Bench"),
        ("lab_bench", "Lab Bench"),
        ("Lab-Bench", "Lab Bench"),
        ("TowerBox-Runner", "TowerBox-Runner"),
        ("  Kiln  ", "Kiln"),
    ],
)
def test_registered_node_names_with_separators_resolve(
    monkeypatch: pytest.MonkeyPatch, header: str, canonical: str
) -> None:
    assert _resolve(monkeypatch, _FLEET, header) == canonical


@pytest.mark.parametrize("header", ["Kiln-2", "Kil", "Kilnx", "local-Kiln", "unknown_node", "OldNode7"])
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
        "../Kiln",
        "Kiln/1",
        "Bri\\ck",
        "-Kiln",
        "Kiln_",
        "---",
        "K" * 65,
        "Kïln",
    ],
)
def test_malformed_names_are_rejected(monkeypatch: pytest.MonkeyPatch, header: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, _FLEET, header)
    assert exc.value.status_code == 403


def test_hub_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, _FLEET, "HubAlpha")
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
    assert canonical_node_token("HubAlpha-Runner") == "hubalpharunner"
    assert canonical_node_token(" Lab_Bench ") == canonical_node_token("lab bench") == "labbench"
    for bad in ("", "a/b", "a\x1fb", "a.b", "-a"):
        with pytest.raises(ValueError, match="node name"):
            canonical_node_token(bad)


def test_runner_pool_of_a_retired_machine_is_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pool entry has no ``retired`` flag of its own; its machine's flag governs."""
    registry = _registry(
        {"name": "OldNode7", "role": "node", "retired": True, "runner_pools": [{"name": "OldNode7-Runner"}]},
    )
    for header in ("OldNode7-Runner", "OldNode7"):
        with pytest.raises(HTTPException) as exc:
            _resolve(monkeypatch, registry, header)
        assert (exc.value.status_code, exc.value.detail) == (403, "unregistered fleet node")


def test_retired_machine_sharing_its_pool_name_is_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool named like its machine replaces the machine in the index; retirement must survive that."""
    registry = _registry({"name": "node-a", "role": "node", "retired": True, "runner_pools": [{"name": "node-a"}]})
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, registry, "node-a")
    assert exc.value.detail == "unregistered fleet node"


def test_runner_pool_of_the_hub_is_refused_like_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry(
        {"name": "HubAlpha", "role": "hub", "runner_pools": [{"name": "HubAlpha-Runner", "role": "runner_pool"}]},
    )
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, registry, "HubAlpha-Runner")
    assert exc.value.detail == "hub cannot request spoke run tokens"
