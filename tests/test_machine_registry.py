"""Tests for backend/machine_registry.py — issue #386."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import machine_registry as mr
import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_registry(path: Path, data: dict) -> None:
    path.write_text(yaml.dump(data), encoding="utf-8")


def _minimal_entry(name: str = "machine-1") -> dict:
    return {"name": name}


# ---------------------------------------------------------------------------
# _normalize_token
# ---------------------------------------------------------------------------


def test_normalize_token_strips_special_chars() -> None:
    assert mr._normalize_token("Machine-1!") == "machine1"


def test_normalize_token_lowercases() -> None:
    assert mr._normalize_token("MyHost") == "myhost"


def test_normalize_token_empty() -> None:
    assert mr._normalize_token("") == ""


# ---------------------------------------------------------------------------
# _coerce_str_list
# ---------------------------------------------------------------------------


def test_coerce_str_list_string() -> None:
    assert mr._coerce_str_list("foo") == ["foo"]


def test_coerce_str_list_list() -> None:
    assert mr._coerce_str_list(["a", "b"]) == ["a", "b"]


def test_coerce_str_list_none() -> None:
    assert mr._coerce_str_list(None) == []


def test_coerce_str_list_filters_empty_strings() -> None:
    assert mr._coerce_str_list(["a", "", "b"]) == ["a", "b"]


def test_coerce_str_list_invalid_type() -> None:
    with pytest.raises(ValueError):
        mr._coerce_str_list(123)


# ---------------------------------------------------------------------------
# _coerce_number
# ---------------------------------------------------------------------------


def test_coerce_number_int() -> None:
    assert mr._coerce_number(16, field="cpu") == 16


def test_coerce_number_float_string() -> None:
    assert mr._coerce_number("64.0", field="mem") == 64


def test_coerce_number_none_returns_none() -> None:
    assert mr._coerce_number(None, field="disk") is None


def test_coerce_number_bool_raises() -> None:
    with pytest.raises(ValueError, match="numeric"):
        mr._coerce_number(True, field="cores")


def test_coerce_number_invalid_string_raises() -> None:
    with pytest.raises(ValueError):
        mr._coerce_number("abc", field="mem")


# ---------------------------------------------------------------------------
# _coerce_bool
# ---------------------------------------------------------------------------


def test_coerce_bool_true_string() -> None:
    assert mr._coerce_bool("true", field="x") is True


def test_coerce_bool_false_string() -> None:
    assert mr._coerce_bool("no", field="x") is False


def test_coerce_bool_already_bool() -> None:
    assert mr._coerce_bool(True, field="x") is True


def test_coerce_bool_none_returns_none() -> None:
    assert mr._coerce_bool(None, field="x") is None


def test_coerce_bool_invalid_raises() -> None:
    with pytest.raises(ValueError):
        mr._coerce_bool("maybe", field="x")


# ---------------------------------------------------------------------------
# _workload_capacity_from_hardware
# ---------------------------------------------------------------------------


def test_workload_capacity_gpu_tag() -> None:
    hw = {"cpu_logical_cores": 8, "memory_gb": 32, "gpu_vram_gb": 24}
    cap = mr._workload_capacity_from_hardware(hw)
    assert "gpu" in cap["tags"]


def test_workload_capacity_parallel_ci_tag() -> None:
    hw = {"cpu_logical_cores": 16, "memory_gb": 16}
    cap = mr._workload_capacity_from_hardware(hw)
    assert "parallel-ci" in cap["tags"]


def test_workload_capacity_small_ci_tag() -> None:
    hw = {"cpu_logical_cores": 2, "memory_gb": 4}
    cap = mr._workload_capacity_from_hardware(hw)
    assert "small-ci" in cap["tags"]


def test_workload_capacity_memory_heavy_tag() -> None:
    hw = {"cpu_logical_cores": 8, "memory_gb": 64}
    cap = mr._workload_capacity_from_hardware(hw)
    assert "memory-heavy" in cap["tags"]


def test_workload_capacity_cpu_slots() -> None:
    hw = {"cpu_logical_cores": 8}
    cap = mr._workload_capacity_from_hardware(hw)
    assert cap["cpu_slots"] == 4


# ---------------------------------------------------------------------------
# _normalize_machine_entry
# ---------------------------------------------------------------------------


def test_normalize_machine_entry_minimal() -> None:
    entry = _minimal_entry("host-a")
    result = mr._normalize_machine_entry(entry)
    assert result["name"] == "host-a"
    assert isinstance(result["aliases"], list)
    assert isinstance(result["tailscale_nodes"], list)


def test_normalize_machine_entry_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        mr._normalize_machine_entry({})


def test_normalize_machine_entry_aliases_coerced() -> None:
    entry = {"name": "h", "aliases": "alias-only"}
    result = mr._normalize_machine_entry(entry)
    assert result["aliases"] == ["alias-only"]


def test_normalize_machine_entry_invalid_maintenance() -> None:
    entry = {"name": "h", "maintenance": "not-a-dict"}
    with pytest.raises(ValueError, match="maintenance"):
        mr._normalize_machine_entry(entry)


def test_normalize_machine_entry_invalid_tailscale_nodes() -> None:
    entry = {"name": "h", "tailscale_nodes": "bad"}
    with pytest.raises(ValueError, match="tailscale_nodes"):
        mr._normalize_machine_entry(entry)


# ---------------------------------------------------------------------------
# load_machine_registry
# ---------------------------------------------------------------------------


def test_load_machine_registry_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MACHINE_REGISTRY_PATH", str(tmp_path / "nonexistent.yml"))
    result = mr.load_machine_registry()
    assert result == {"version": 1, "machines": []}


def test_load_machine_registry_empty_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "registry.yml"
    _write_registry(p, {"version": 1, "machines": []})
    # Bypass path security checks to allow tmp_path
    with (
        patch("machine_registry.validate_config_path", return_value=p),
        patch("machine_registry.safe_yaml_load", return_value={"version": 1, "machines": []}),
    ):
        result = mr.load_machine_registry(path=p)
    assert result["machines"] == []
    assert result["version"] == 1


def test_load_machine_registry_single_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "registry.yml"
    _write_registry(p, {"version": 1, "machines": [{"name": "build-01"}]})
    raw = {"version": 1, "machines": [{"name": "build-01"}]}
    with (
        patch("machine_registry.validate_config_path", return_value=p),
        patch("machine_registry.safe_yaml_load", return_value=raw),
    ):
        result = mr.load_machine_registry(path=p)
    assert len(result["machines"]) == 1
    assert result["machines"][0]["name"] == "build-01"


def test_load_machine_registry_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"version": 1, "machines": [{"name": "json-host"}]}), encoding="utf-8")
    with patch("machine_registry.validate_config_path", return_value=p):
        result = mr.load_machine_registry(path=p)
    assert result["machines"][0]["name"] == "json-host"


# ---------------------------------------------------------------------------
# build_machine_registry_index
# ---------------------------------------------------------------------------


def test_build_index_canonical_name() -> None:
    registry = {"machines": [{"name": "Host-1", "aliases": []}]}
    index = mr.build_machine_registry_index(registry)
    assert "host1" in index


def test_build_index_aliases_included() -> None:
    registry = {"machines": [{"name": "Host-1", "aliases": ["h1", "builder"]}]}
    index = mr.build_machine_registry_index(registry)
    assert "h1" in index
    assert "builder" in index


def test_build_index_empty_registry() -> None:
    assert mr.build_machine_registry_index({"machines": []}) == {}


# ---------------------------------------------------------------------------
# merge_registry_with_live_nodes
# ---------------------------------------------------------------------------


def test_merge_registry_known_node_gets_registry_key() -> None:
    live = [{"name": "Host-1", "status": "online"}]
    registry = {"machines": [{"name": "Host-1", "aliases": [], "hardware": {}}]}
    merged = mr.merge_registry_with_live_nodes(live, registry)
    assert len(merged) == 1
    assert "registry" in merged[0]


def test_merge_registry_unknown_node_no_registry_key() -> None:
    live = [{"name": "unknown-host", "status": "online"}]
    registry = {"machines": [{"name": "Host-1", "aliases": [], "hardware": {}}]}
    merged = mr.merge_registry_with_live_nodes(live, registry)
    assert merged[0].get("registry") is None


def test_merge_registry_offline_placeholder_included() -> None:
    """Registry-only machines not in live list appear as offline placeholders."""
    live: list = []
    registry = {"machines": [{"name": "offline-host", "aliases": [], "hardware": {}}]}
    merged = mr.merge_registry_with_live_nodes(live, registry)
    assert any(m.get("name") == "offline-host" for m in merged)


# ---------------------------------------------------------------------------
# Path validation — issue: the YAML ships under backend/ but the security
# validator only allowed ~/.config/runner-dashboard/ + git-repo-root. On
# deployed installs (which are not git checkouts) every load silently failed
# with "Config path escapes allowed roots", breaking fleet federation.
# ---------------------------------------------------------------------------


def test_load_machine_registry_from_module_dir(monkeypatch) -> None:
    """The registry must load when the YAML lives next to machine_registry.py,
    even when there is no git repo and no ~/.config/runner-dashboard/.

    Reproduces the deployed-install case where update-deployed.sh copies
    backend/ next to where the runtime expects machine_registry.yml. The
    previous code only allowed ~/.config/runner-dashboard/ + git-repo-root,
    so deployed hosts (not git checkouts) silently failed every load with
    'Config path escapes allowed roots'.
    """
    import machine_registry as mr_module

    module_dir = Path(mr_module.__file__).resolve().parent
    target = module_dir / "_pytest_machine_registry.yml"
    target.write_text(
        "version: 1\n"
        "machines:\n"
        "  - name: test-machine\n"
        "    aliases: [test]\n"
        "    dashboard_url: http://100.0.0.1:8321\n"
        "    tailscale_nodes:\n"
        "      - name: test-node\n"
        "        ip: 100.0.0.1\n",
        encoding="utf-8",
    )
    # Windows-mounted filesystems can't represent POSIX mode bits, so the
    # world-writable check fires spuriously. The check itself is covered
    # by security.py's own tests; here we just need to verify the path
    # passes the allowed-roots check.
    import security as _security

    monkeypatch.setattr(_security, "_check_file_mode", lambda _p: True)
    try:
        result = mr.load_machine_registry(path=target)
        assert result["version"] == 1
        assert len(result["machines"]) == 1
        assert result["machines"][0]["name"] == "test-machine"
    finally:
        target.unlink(missing_ok=True)


def test_load_machine_registry_rejects_unrelated_path(tmp_path: Path, monkeypatch) -> None:
    """The validator must still reject paths outside the allowed roots.

    A regression of this would defeat the security check entirely.
    """
    rogue = tmp_path / "rogue.yml"
    rogue.write_text("version: 1\nmachines: []\n", encoding="utf-8")
    import pytest
    import security as _security

    monkeypatch.setattr(_security, "_check_file_mode", lambda _p: True)
    with pytest.raises(ValueError, match="escapes allowed roots"):
        mr.load_machine_registry(path=rogue)


def test_load_machine_registry_missing_path_returns_empty(tmp_path: Path) -> None:
    """A missing file is benign — return an empty registry, don't raise."""
    result = mr.load_machine_registry(path=tmp_path / "absent.yml")
    assert result == {"version": 1, "machines": []}
