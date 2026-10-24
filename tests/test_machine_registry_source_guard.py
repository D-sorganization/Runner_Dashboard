"""Registry source guards for public-repo documentation topology."""

from __future__ import annotations

from pathlib import Path

import machine_registry as mr
import pytest
import yaml


def test_empty_shipped_registry_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = tmp_path / "machine_registry.yml"
    shipped.write_text(
        yaml.dump({"version": 1, "topology": "documentation", "machines": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mr, "DEFAULT_REGISTRY_PATH", shipped)
    loaded = mr.load_machine_registry(shipped)
    assert loaded["machines"] == []


def test_documentation_machines_on_shipped_path_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = tmp_path / "machine_registry.yml"
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
    with pytest.raises(RuntimeError, match="documentation-only"):
        mr.load_machine_registry(shipped)


def test_require_local_registry_fails_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = tmp_path / "machine_registry.yml"
    shipped.write_text(yaml.dump({"version": 1, "machines": []}), encoding="utf-8")
    local = tmp_path / "missing" / "machine_registry.yml"
    monkeypatch.setattr(mr, "DEFAULT_REGISTRY_PATH", shipped)
    monkeypatch.setattr(mr, "LOCAL_REGISTRY_PATH", local)
    monkeypatch.setenv("RUNNER_DASHBOARD_REQUIRE_LOCAL_REGISTRY", "1")
    with pytest.raises(RuntimeError, match="canonical local registry"):
        mr.load_machine_registry(None)
