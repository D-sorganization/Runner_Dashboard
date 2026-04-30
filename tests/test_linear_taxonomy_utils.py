"""Tests for linear_taxonomy_map.py pure mapping helpers.

Covers load_mapping_config, derived_labels, apply_mapping, and the
internal helpers _labels_for_estimate, _linear_label_names, _dedupe_stable.
All logic is pure (no I/O beyond the config file path in load_mapping_config).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

import linear_taxonomy_map as ltm  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_MAPPING = {
    "priority": {"1": ["priority:critical"], "2": ["priority:high"]},
    "estimate": {"1": ["effort:small"], "3": ["effort:medium"], "8": ["effort:large"]},
    "state_type": {"started": ["status:in-progress"], "completed": ["status:done"]},
    "label_aliases": {"bug": ["type:bug"], "Feature Request": ["type:feature"]},
    "label_passthrough_prefixes": ["domain:", "type:"],
    "default_judgement": "objective",
}

MINIMAL_CONFIG = {
    "workspaces": [
        {
            "id": "ws-1",
            "auth": {"kind": "api_key", "env": "LINEAR_API_KEY"},
            "teams": ["engineering"],
            "mapping": "default",
            "trigger_label": "Runner Dashboard",
            "webhook_secret_env": "LINEAR_WEBHOOK_SECRET",
            "default_repository": "D-sorganization/Runner_Dashboard",
            "prefer_source": "linear",
        }
    ],
    "mappings": {"default": MINIMAL_MAPPING},
}


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "linear_map.json"
    path.write_text(json.dumps(MINIMAL_CONFIG), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# load_mapping_config
# ---------------------------------------------------------------------------


def test_load_mapping_config_valid(config_file: Path) -> None:
    result = ltm.load_mapping_config(config_file)
    assert "workspaces" in result
    assert "mappings" in result


def test_load_mapping_config_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Linear config JSON"):
        ltm.load_mapping_config(bad)


def test_load_mapping_config_missing_workspaces(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"mappings": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="workspaces"):
        ltm.load_mapping_config(path)


def test_load_mapping_config_missing_mappings(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"workspaces": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="mappings"):
        ltm.load_mapping_config(path)


def test_load_mapping_config_workspace_missing_field(tmp_path: Path) -> None:
    bad_config = dict(MINIMAL_CONFIG)
    bad_ws = dict(MINIMAL_CONFIG["workspaces"][0])
    del bad_ws["auth"]
    bad_config["workspaces"] = [bad_ws]
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(bad_config), encoding="utf-8")
    with pytest.raises(ValueError, match="auth"):
        ltm.load_mapping_config(path)


def test_load_mapping_config_workspace_unknown_mapping_ref(tmp_path: Path) -> None:
    bad_config = dict(MINIMAL_CONFIG)
    bad_ws = dict(MINIMAL_CONFIG["workspaces"][0])
    bad_ws = {**bad_ws, "mapping": "nonexistent"}
    bad_config = {**bad_config, "workspaces": [bad_ws]}
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(bad_config), encoding="utf-8")
    with pytest.raises(ValueError, match="missing mapping"):
        ltm.load_mapping_config(path)


def test_load_mapping_config_wrong_auth_kind(tmp_path: Path) -> None:
    bad_config = {
        **MINIMAL_CONFIG,
        "workspaces": [{**MINIMAL_CONFIG["workspaces"][0], "auth": {"kind": "oauth", "env": "MY_ENV"}}],
    }
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(bad_config), encoding="utf-8")
    with pytest.raises(ValueError, match="api_key"):
        ltm.load_mapping_config(path)


# ---------------------------------------------------------------------------
# derived_labels — priority, estimate, state_type
# ---------------------------------------------------------------------------


def test_derived_labels_priority_mapped() -> None:
    issue = {"priority": 1, "estimate": None}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "priority:critical" in labels


def test_derived_labels_estimate_exact_match() -> None:
    issue = {"estimate": 3}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "effort:medium" in labels


def test_derived_labels_estimate_bucket_fallback() -> None:
    # estimate=4 is not in config; nearest lower bucket is 3
    issue = {"estimate": 4}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "effort:medium" in labels


def test_derived_labels_estimate_high_picks_largest_bucket() -> None:
    # estimate=10 -> largest bucket <= 10 is 8
    issue = {"estimate": 10}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "effort:large" in labels


def test_derived_labels_state_type_mapped() -> None:
    issue = {"state": {"type": "started"}}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "status:in-progress" in labels


def test_derived_labels_alias_applied() -> None:
    issue = {"labels": {"nodes": [{"name": "bug"}]}}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "type:bug" in labels


def test_derived_labels_passthrough_applied() -> None:
    issue = {"labels": {"nodes": [{"name": "domain:backend"}]}}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "domain:backend" in labels


def test_derived_labels_default_judgement_added_when_none() -> None:
    issue = {}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert any(label.startswith("judgement:") for label in labels)
    assert "judgement:objective" in labels


def test_derived_labels_no_duplicate_labels() -> None:
    # Two labels that would map to the same output
    issue = {"priority": 1, "estimate": 3, "labels": {"nodes": [{"name": "bug"}, {"name": "bug"}]}}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert len(labels) == len(set(labels))


def test_derived_labels_unknown_labels_ignored() -> None:
    issue = {"labels": {"nodes": [{"name": "some-random-label"}]}}
    labels = ltm.derived_labels(issue, MINIMAL_MAPPING)
    assert "some-random-label" not in labels


# ---------------------------------------------------------------------------
# apply_mapping — taxonomy integration
# ---------------------------------------------------------------------------


def test_apply_mapping_returns_mapping_result_keys() -> None:
    issue = {"priority": 1, "estimate": 3}
    result = ltm.apply_mapping(issue, MINIMAL_MAPPING)
    for key in (
        "type",
        "complexity",
        "effort",
        "judgement",
        "quick_win",
        "panel_review",
        "domains",
        "wave",
        "derived_labels",
        "source_signals",
    ):
        assert key in result


def test_apply_mapping_source_signals_present() -> None:
    issue = {"priority": 2, "estimate": 8}
    result = ltm.apply_mapping(issue, MINIMAL_MAPPING)
    ss = result["source_signals"]
    assert ss["priority"] == 2
    assert ss["estimate"] == 8


def test_apply_mapping_derived_labels_in_result() -> None:
    issue = {"state": {"type": "completed"}}
    result = ltm.apply_mapping(issue, MINIMAL_MAPPING)
    assert "status:done" in result["derived_labels"]


# ---------------------------------------------------------------------------
# _linear_label_names — label shape handling
# ---------------------------------------------------------------------------


def test_linear_label_names_graphql_nodes_shape() -> None:
    issue = {"labels": {"nodes": [{"name": "bug"}, {"name": "type:feature"}]}}
    names = ltm._linear_label_names(issue)
    assert names == ["bug", "type:feature"]


def test_linear_label_names_flat_list_of_dicts() -> None:
    issue = {"labels": [{"name": "bug"}]}
    names = ltm._linear_label_names(issue)
    assert names == ["bug"]


def test_linear_label_names_flat_list_of_strings() -> None:
    issue = {"labels": ["bug", "type:feature"]}
    names = ltm._linear_label_names(issue)
    assert names == ["bug", "type:feature"]


def test_linear_label_names_no_labels() -> None:
    assert ltm._linear_label_names({}) == []


def test_linear_label_names_null_labels() -> None:
    assert ltm._linear_label_names({"labels": None}) == []


# ---------------------------------------------------------------------------
# _dedupe_stable
# ---------------------------------------------------------------------------


def test_dedupe_stable_preserves_order() -> None:
    labels = ["c", "a", "b", "a", "c"]
    assert ltm._dedupe_stable(labels) == ["c", "a", "b"]


def test_dedupe_stable_empty() -> None:
    assert ltm._dedupe_stable([]) == []


def test_dedupe_stable_no_duplicates() -> None:
    labels = ["x", "y", "z"]
    assert ltm._dedupe_stable(labels) == labels
