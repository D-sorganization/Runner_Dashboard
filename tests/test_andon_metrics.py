"""Unit tests for andon and claim-gate metrics exporter (#2904)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import andon_metrics  # noqa: E402
from scripts import ci_slo_check as slo  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "fixtures" / "andon_metrics" / "baseline_2026_10_26.json"


def _load_baseline_raw() -> dict[str, Any]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_baseline_replay_matches_epic_snapshot() -> None:
    raw = _load_baseline_raw()
    dataset = andon_metrics.AndonDataset.from_mapping(raw)
    metrics = andon_metrics.compute_andon_metrics(dataset)

    assert metrics.andon_backlog == 12
    assert metrics.open_prs_total == 64
    assert metrics.cursor_open_prs == 64
    assert metrics.claimed_pr_count == 2
    assert metrics.unlinked_pr_share == pytest.approx(0.96875)
    assert metrics.unlinked_pr_pct == pytest.approx(96.875)
    assert metrics.open_prs_per_issue_max == 16
    assert metrics.duplicate_fix_prs_per_fingerprint_max == 2
    assert metrics.duplicate_fix_prs_total == 3
    assert metrics.open_andons_per_fingerprint_max == 2
    assert metrics.andon_max_age_hours == pytest.approx(18.0)
    assert metrics.andon_age_p90_hours > 0.0
    assert metrics.andon_to_card_p90_hours > 0.0
    assert metrics.red_main_ttff_p90_minutes == pytest.approx(36.4, abs=0.2)


def test_publish_hub_snapshot_uses_ci_slo_cache_key_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    assert andon_metrics.HUB_CACHE_KEY == "andon_metrics"
    assert andon_metrics.HUB_CACHE_KEY != slo.HUB_CACHE_KEY

    dataset = andon_metrics.AndonDataset.from_mapping(_load_baseline_raw())
    metrics = andon_metrics.compute_andon_metrics(dataset)

    captured: dict[str, Any] = {}

    def fake_cache_set(key: str, payload: dict[str, Any]) -> None:
        captured["key"] = key
        captured["payload"] = payload

    import types

    fake_module = types.ModuleType("cache_utils")
    fake_module.cache_set = fake_cache_set  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cache_utils", fake_module)

    published = andon_metrics.publish_hub_snapshot(metrics, ok=False, detail={"source": "baseline"})
    assert captured["key"] == "andon_metrics"
    assert captured["payload"] == published
    assert published["ok"] is False
    assert published["detail"] == {"source": "baseline"}
    assert "recorded_at" in published and published["recorded_at"].endswith("Z")
    assert published["metrics"]["andon_backlog"] == 12
    assert published["metrics"]["unlinked_pr_share"] == pytest.approx(0.96875)


def test_malformed_fixture_raises_value_error() -> None:
    with pytest.raises(ValueError, match="as_of"):
        andon_metrics.AndonDataset.from_mapping({"open_andons": [], "open_prs": []})

    with pytest.raises(ValueError, match="open_andons"):
        andon_metrics.AndonDataset.from_mapping({"as_of": "2026-10-26T16:00:00Z", "open_prs": []})

    with pytest.raises(ValueError, match="created_at"):
        andon_metrics.AndonDataset.from_mapping(
            {
                "as_of": "2026-10-26T16:00:00Z",
                "open_andons": [{"number": 1}],
                "open_prs": [],
            }
        )


def test_publish_hub_snapshot_surfaces_cache_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = andon_metrics.AndonDataset.from_mapping(_load_baseline_raw())
    metrics = andon_metrics.compute_andon_metrics(dataset)

    import types

    def broken_cache_set(key: str, payload: dict[str, Any]) -> None:
        raise OSError("disk full")

    fake_module = types.ModuleType("cache_utils")
    fake_module.cache_set = broken_cache_set  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cache_utils", fake_module)

    with pytest.raises(RuntimeError, match="disk full"):
        andon_metrics.publish_hub_snapshot(metrics, ok=True)


def test_cli_fixture_replay_and_enforce_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    rc_report = andon_metrics.main(["--fixture", str(BASELINE)])
    assert rc_report == 0
    out = json.loads(capsys.readouterr().out)
    assert out["metrics"]["andon_backlog"] == 12
    assert out["metrics"]["unlinked_pr_share"] == pytest.approx(0.96875)
    assert len(out["breaches"]) >= 4

    rc_enforce = andon_metrics.main(["--fixture", str(BASELINE), "--enforce"])
    assert rc_enforce == 1
