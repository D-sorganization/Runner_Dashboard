"""Tests for A5 andon and claim-gate metrics exporter (#2904)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts import andon_metrics as am  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "fixtures" / "andon_metrics" / "baseline_2026_10_26.json"


def _load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_baseline_replay_matches_epic_snapshot() -> None:
    raw = _load_baseline()
    dataset = am.AndonDataset.from_mapping(raw)
    metrics = am.compute_andon_metrics(dataset)

    assert metrics.andon_backlog == 12
    assert metrics.open_pr_count == 64
    assert metrics.unlinked_pr_count == 62
    assert metrics.unlinked_pr_share == pytest.approx(0.96875, abs=1e-5)
    assert metrics.max_open_prs_per_issue == 16
    assert metrics.duplicate_fix_prs == 16
    assert metrics.max_prs_per_fingerprint == 16
    assert metrics.duplicate_open_andons == 1
    assert metrics.andon_max_age_minutes == pytest.approx(900.0)
    assert metrics.andon_age_p90_minutes > 0.0
    assert metrics.andon_to_card_p90_minutes == pytest.approx(243.0, abs=1.0)
    assert metrics.red_main_ttff_p90_minutes == pytest.approx(34.8, abs=0.5)
    assert am.run_check(dataset, mode="full") == 1
    assert am.main(["--fixture", str(BASELINE), "--mode", "report"]) == 0


def test_publish_hub_snapshot_uses_ci_slo_cache_key_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    import cache_utils  # noqa: PLC0415

    captured: dict[str, Any] = {}

    def fake_cache_set(key: str, value: Any) -> None:
        captured["key"] = key
        captured["value"] = value

    monkeypatch.setattr(cache_utils, "cache_set", fake_cache_set)

    dataset = am.AndonDataset.from_mapping(_load_baseline())
    metrics = am.compute_andon_metrics(dataset)
    snapshot = am.publish_hub_snapshot(metrics, ok=False, detail={"breaches": ["unlinked_pr_share"]})

    assert am.HUB_CACHE_KEY == "andon_metrics"
    assert captured["key"] == "andon_metrics"
    assert captured["value"] == snapshot
    assert snapshot["ok"] is False
    assert "recorded_at" in snapshot
    assert snapshot["metrics"]["andon_backlog"] == 12
    assert snapshot["metrics"]["unlinked_pr_share"] == pytest.approx(0.96875, abs=1e-5)


def test_publish_hub_snapshot_raises_on_cache_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import cache_utils  # noqa: PLC0415

    def broken_cache_set(key: str, value: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cache_utils, "cache_set", broken_cache_set)
    metrics = am.compute_andon_metrics(_load_baseline())
    with pytest.raises(RuntimeError, match="Failed to publish andon_metrics hub snapshot"):
        am.publish_hub_snapshot(metrics, ok=True)


@pytest.mark.parametrize(
    "bad_payload",
    [
        {},
        {"as_of": "not-a-timestamp", "open_andons": [], "open_prs": []},
        {"as_of": "2026-10-26T16:00:00Z", "open_andons": "bad", "open_prs": []},
        {"as_of": "2026-10-26T16:00:00Z", "open_andons": [{"number": 1}], "open_prs": []},
        {"as_of": "2026-10-26T16:00:00Z", "open_andons": [], "open_prs": [{"issue_number": 1}]},
    ],
)
def test_malformed_dataset_raises_value_error(bad_payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        am.AndonDataset.from_mapping(bad_payload)


def test_healthy_dataset_passes_targets_and_renders_prometheus() -> None:
    from routers.metrics import render_prometheus_text  # noqa: PLC0415

    healthy = am.AndonDataset.from_mapping(
        {
            "as_of": "2026-10-26T16:00:00Z",
            "open_andons": [
                {
                    "number": 2950,
                    "fingerprint": "fp-clean-1",
                    "opened_at": "2026-10-26T15:30:00Z",
                    "card_created_at": "2026-10-26T15:40:00Z",
                }
            ],
            "open_prs": [
                {
                    "number": 3100,
                    "issue_number": 2950,
                    "fingerprint": "fp-clean-1",
                    "linked_claimed_card": True,
                }
            ],
            "red_main_incidents": [
                {
                    "fingerprint": "fp-main-clean",
                    "detected_at": "2026-10-26T15:00:00Z",
                    "first_fix_pr_at": "2026-10-26T15:10:00Z",
                }
            ],
        }
    )
    metrics = am.compute_andon_metrics(healthy)
    assert metrics.unlinked_pr_share == 0.0
    assert metrics.duplicate_fix_prs == 0
    assert metrics.max_open_prs_per_issue == 1
    assert am.run_check(healthy, mode="full") == 0

    hub_snap = am.build_hub_snapshot(metrics, ok=True)
    text = render_prometheus_text({"andon": hub_snap})
    assert "rd_andon_backlog_total 1" in text
    assert "rd_andon_unlinked_pr_share 0.0000" in text
    assert "rd_andon_duplicate_fix_prs_total 0" in text
