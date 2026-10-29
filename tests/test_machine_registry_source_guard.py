"""Registry source guards for public-repo documentation topology."""

from __future__ import annotations

from pathlib import Path

import machine_registry as mr
import pytest
import yaml

_BACKEND = Path(__file__).resolve().parents[1] / "backend"


def _shipped_fixture(name: str) -> Path:
    """Registry path under ``backend/`` so ``validate_config_path`` accepts it."""
    return _BACKEND / name


def test_empty_shipped_registry_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = _shipped_fixture("_pytest_shipped_empty_registry.yml")
    shipped.write_text(
        yaml.dump({"version": 1, "topology": "documentation", "machines": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mr, "DEFAULT_REGISTRY_PATH", shipped)
    try:
        loaded = mr.load_machine_registry(shipped)
        assert loaded["machines"] == []
    finally:
        shipped.unlink(missing_ok=True)


def test_documentation_machines_on_shipped_path_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = _shipped_fixture("_pytest_shipped_live_registry.yml")
    shipped.write_text(
        yaml.dump(
            {
                "version": 1,
                "machines": [
                    {
                        "name": "hub-a",
                        "dashboard_url": "http://hub-a.fleet-example.ts.net:8321",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mr, "DEFAULT_REGISTRY_PATH", shipped)
    try:
        with pytest.raises(RuntimeError, match="documentation-only"):
            mr.load_machine_registry(shipped)
    finally:
        shipped.unlink(missing_ok=True)


def test_require_local_registry_fails_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = _shipped_fixture("_pytest_shipped_require_local.yml")
    shipped.write_text(yaml.dump({"version": 1, "machines": []}), encoding="utf-8")
    local = _BACKEND / "_pytest_missing_local_registry.yml"
    monkeypatch.setattr(mr, "DEFAULT_REGISTRY_PATH", shipped)
    monkeypatch.setattr(mr, "LOCAL_REGISTRY_PATH", local)
    monkeypatch.setenv("RUNNER_DASHBOARD_REQUIRE_LOCAL_REGISTRY", "1")
    try:
        with pytest.raises(RuntimeError, match="canonical local registry"):
            mr.load_machine_registry(None)
    finally:
        shipped.unlink(missing_ok=True)
