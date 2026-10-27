"""Grok Bot role bundle export, drift, and redaction (#3099 slice 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from staff.grok_role_bundle import (
    BUNDLE_FORMAT,
    apply_grok_import_report,
    build_role_bundle,
    build_role_manifest,
    canonical_payload_hash,
    check_bundle_tree_drift,
    export_role_bundles,
    grok_import_drift,
    load_bundle_tree,
)
from staff.role_export_redaction import assert_bundle_export_safe, scrub_role_export_payload

_FIXTURE_ROLES = Path(__file__).resolve().parents[1] / "fixtures" / "grok_role_sync" / "roles"
_REPO_BUNDLES = Path(__file__).resolve().parents[2] / "config" / "grok_role_bundles"


@pytest.mark.unit
def test_export_is_deterministic_for_same_role_file(tmp_path: Path) -> None:
    role_path = _FIXTURE_ROLES / "sync-canary.yml"
    first = build_role_bundle(role_path)
    second = build_role_bundle(role_path)
    assert first.payload_hash == second.payload_hash
    assert first.source_content_hash == second.source_content_hash
    assert first.payload == second.payload


@pytest.mark.unit
def test_canonical_payload_hash_stable_across_key_order() -> None:
    a = {"role": "sync-canary", "z": 1, "persona": {"voice": "neutral"}}
    b = {"persona": {"voice": "neutral"}, "role": "sync-canary", "z": 1}
    assert canonical_payload_hash(a) == canonical_payload_hash(b)


@pytest.mark.unit
def test_drift_check_fails_when_role_changes_without_reexport(tmp_path: Path) -> None:
    roles = tmp_path / "roles"
    bundles = tmp_path / "bundles"
    roles.mkdir()
    role_path = roles / "sync-canary.yml"
    role_path.write_text((_FIXTURE_ROLES / "sync-canary.yml").read_text(encoding="utf-8"), encoding="utf-8")
    export_role_bundles(roles, bundles)
    assert check_bundle_tree_drift(roles, bundles) == []

    role_path.write_text(
        role_path.read_text(encoding="utf-8").replace("neutral", "updated-voice"),
        encoding="utf-8",
    )
    drift = check_bundle_tree_drift(roles, bundles)
    assert drift == ["sync-canary"]


@pytest.mark.unit
def test_committed_fixture_bundles_match_canary_role() -> None:
    if not _REPO_BUNDLES.is_dir():
        pytest.skip("bundle tree not committed yet")
    drift = check_bundle_tree_drift(_FIXTURE_ROLES, _REPO_BUNDLES)
    assert drift == []


@pytest.mark.unit
def test_bundle_excludes_private_context_and_household_fields() -> None:
    raw = yaml.safe_load((_FIXTURE_ROLES / "sync-canary.yml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    raw["private_context"] = {"mail": ["category:finance"], "household": {"member": "redacted"}}
    raw["scope"] = {**raw.get("scope", {}), "household_calendar": True}
    scrubbed = scrub_role_export_payload(raw)
    bundle = build_role_bundle(_FIXTURE_ROLES / "sync-canary.yml", raw_override=scrubbed)
    assert_bundle_export_safe(bundle.payload)
    blob = json.dumps(bundle.payload)
    assert "private_context" not in blob
    assert "household" not in blob.lower()
    assert "household_calendar" not in blob


@pytest.mark.unit
def test_grok_import_report_surfaces_payload_drift() -> None:
    role_path = _FIXTURE_ROLES / "sync-canary.yml"
    bundle = build_role_bundle(role_path)
    manifest = build_role_manifest([bundle])
    updated = apply_grok_import_report(manifest, "sync-canary", payload_hash="0" * 64)
    assert grok_import_drift(updated) == [
        {"role": "sync-canary", "expected": bundle.payload_hash, "reported": "0" * 64}
    ]


@pytest.mark.unit
def test_load_bundle_tree_requires_manifest_format() -> None:
    root = _REPO_BUNDLES
    if not root.is_dir():
        pytest.skip("bundle tree not committed yet")
    tree = load_bundle_tree(root)
    assert tree.manifest["bundle_format"] == BUNDLE_FORMAT
