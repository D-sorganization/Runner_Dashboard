"""Tests for backend/usage_monitoring.py — issue #386."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import usage_monitoring as um

# ---------------------------------------------------------------------------
# _coerce_float
# ---------------------------------------------------------------------------


def test_coerce_float_int() -> None:
    assert um._coerce_float(10) == pytest.approx(10.0)


def test_coerce_float_string() -> None:
    assert um._coerce_float("3.14") == pytest.approx(3.14)


def test_coerce_float_none() -> None:
    assert um._coerce_float(None) is None


def test_coerce_float_bool_raises() -> None:
    with pytest.raises(TypeError):
        um._coerce_float(True)


def test_coerce_float_empty_string_returns_none() -> None:
    assert um._coerce_float("") is None


# ---------------------------------------------------------------------------
# _coerce_confidence
# ---------------------------------------------------------------------------


def test_coerce_confidence_clamps_high() -> None:
    assert um._coerce_confidence(1.5) == pytest.approx(1.0)


def test_coerce_confidence_clamps_low() -> None:
    assert um._coerce_confidence(-0.5) == pytest.approx(0.0)


def test_coerce_confidence_none_returns_default() -> None:
    assert um._coerce_confidence(None) == pytest.approx(0.5)


def test_coerce_confidence_valid() -> None:
    assert um._coerce_confidence(0.8) == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# _normalize_timestamp
# ---------------------------------------------------------------------------


def test_normalize_timestamp_none() -> None:
    assert um._normalize_timestamp(None) is None


def test_normalize_timestamp_empty() -> None:
    assert um._normalize_timestamp("") is None


def test_normalize_timestamp_iso_z() -> None:
    result = um._normalize_timestamp("2026-01-15T12:00:00Z")
    assert result is not None
    assert result.endswith("Z")


def test_normalize_timestamp_invalid_raises() -> None:
    with pytest.raises(TypeError):
        um._normalize_timestamp(12345)


# ---------------------------------------------------------------------------
# UsageSourceConfig.from_dict
# ---------------------------------------------------------------------------


def _minimal_source() -> dict:
    return {"name": "github-actions", "kind": "ci-minutes"}


def test_usage_source_config_from_dict_minimal() -> None:
    src = um.UsageSourceConfig.from_dict(_minimal_source())
    assert src.name == "github-actions"
    assert src.kind == "ci-minutes"
    assert src.confidence == pytest.approx(0.5)


def test_usage_source_config_from_dict_missing_name_raises() -> None:
    with pytest.raises(ValueError, match="name"):
        um.UsageSourceConfig.from_dict({"kind": "ci-minutes"})


def test_usage_source_config_from_dict_missing_kind_raises() -> None:
    with pytest.raises(ValueError, match="kind"):
        um.UsageSourceConfig.from_dict({"name": "x"})


def test_usage_source_config_from_dict_with_usage_limit() -> None:
    data = {**_minimal_source(), "usage_limit": 1000, "current_usage": 400}
    src = um.UsageSourceConfig.from_dict(data)
    assert src.usage_limit == pytest.approx(1000.0)
    assert src.current_usage == pytest.approx(400.0)


def test_usage_source_config_from_dict_confidence_clamped() -> None:
    data = {**_minimal_source(), "confidence": 2.5}
    src = um.UsageSourceConfig.from_dict(data)
    assert src.confidence == pytest.approx(1.0)


def test_usage_source_config_to_dict_round_trips() -> None:
    data = {**_minimal_source(), "usage_limit": 500, "current_usage": 100}
    src = um.UsageSourceConfig.from_dict(data)
    d = src.to_dict()
    assert d["name"] == "github-actions"
    assert d["usage_limit"] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# parse_usage_sources_config
# ---------------------------------------------------------------------------


def test_parse_usage_sources_config_list() -> None:
    sources = um.parse_usage_sources_config([_minimal_source()])
    assert len(sources) == 1
    assert sources[0].name == "github-actions"


def test_parse_usage_sources_config_dict_wrapper() -> None:
    sources = um.parse_usage_sources_config({"usage_sources": [_minimal_source()]})
    assert len(sources) == 1


def test_parse_usage_sources_config_not_list_raises() -> None:
    with pytest.raises(TypeError):
        um.parse_usage_sources_config({"usage_sources": "bad"})


def test_parse_usage_sources_config_empty_list() -> None:
    assert um.parse_usage_sources_config([]) == []


# ---------------------------------------------------------------------------
# load_usage_sources_config
# ---------------------------------------------------------------------------


def test_load_usage_sources_config_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USAGE_SOURCES_PATH", str(tmp_path / "nonexistent.json"))
    result = um.load_usage_sources_config()
    assert result == []


def test_load_usage_sources_config_from_file(tmp_path: Path) -> None:
    p = tmp_path / "sources.json"
    p.write_text(json.dumps([_minimal_source()]), encoding="utf-8")
    result = um.load_usage_sources_config(path=p)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# normalize_usage_source
# ---------------------------------------------------------------------------


def test_normalize_usage_source_remaining_computed() -> None:
    src = um.UsageSourceConfig.from_dict({**_minimal_source(), "usage_limit": 1000, "current_usage": 300})
    out = um.normalize_usage_source(src)
    assert out["remaining"] == pytest.approx(700.0)


def test_normalize_usage_source_no_limit_remaining_none() -> None:
    src = um.UsageSourceConfig.from_dict({**_minimal_source(), "current_usage": 300})
    out = um.normalize_usage_source(src)
    assert out["remaining"] is None


def test_normalize_usage_source_label_falls_back_to_name() -> None:
    src = um.UsageSourceConfig.from_dict(_minimal_source())
    out = um.normalize_usage_source(src)
    assert out["label"] == "github-actions"


def test_normalize_usage_source_projected_burn_defaults_to_current_usage() -> None:
    src = um.UsageSourceConfig.from_dict({**_minimal_source(), "current_usage": 200})
    out = um.normalize_usage_source(src)
    assert out["projected_burn"] == pytest.approx(200.0)


def test_normalize_usage_source_observed_at_included() -> None:
    import datetime

    src = um.UsageSourceConfig.from_dict(_minimal_source())
    obs = datetime.datetime(2026, 5, 1, 12, 0, 0, tzinfo=datetime.UTC)
    out = um.normalize_usage_source(src, observed_at=obs)
    assert "observed_at" in out
    assert "2026-05-01" in out["observed_at"]


# ---------------------------------------------------------------------------
# normalize_usage_summary
# ---------------------------------------------------------------------------


def test_normalize_usage_summary_structure() -> None:
    data = [
        {**_minimal_source(), "usage_limit": 1000, "current_usage": 500},
        {"name": "docker-hub", "kind": "pulls", "usage_limit": 200, "current_usage": 50},
    ]
    summary = um.normalize_usage_summary(data)
    assert "usage_sources" in summary
    assert len(summary["usage_sources"]) == 2
    assert "schema_version" in summary
    assert "summary" in summary


def test_normalize_usage_summary_mixed_period() -> None:
    """When sources have different period labels the aggregate summary is 'mixed'."""
    data = [
        {**_minimal_source(), "current_period": {"label": "April"}},
        {"name": "x", "kind": "pulls", "current_period": {"label": "May"}},
    ]
    summary = um.normalize_usage_summary(data)
    assert summary["summary"].get("current_period") == "mixed"


def test_normalize_usage_summary_uniform_period() -> None:
    data = [
        {**_minimal_source(), "current_period": {"label": "April"}},
        {"name": "x", "kind": "pulls", "current_period": {"label": "April"}},
    ]
    summary = um.normalize_usage_summary(data)
    assert summary["summary"].get("current_period") == "April"
