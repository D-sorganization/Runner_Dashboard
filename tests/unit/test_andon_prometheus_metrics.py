"""Tests for Prometheus `/api/metrics` export of `andon_metrics` hub cache (#2904)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers.metrics import collect_metrics_snapshot, render_prometheus_text  # noqa: E402


def test_render_prometheus_text_includes_andon_metrics() -> None:
    snapshot: dict[str, Any] = {
        "andon": {
            "ok": False,
            "metrics": {
                "andon_backlog": 12,
                "andon_to_card_p90_minutes": 185.5,
                "duplicate_fix_prs_total": 16,
                "open_prs_per_issue_max": 16,
                "unlinked_pr_share": 0.96875,
                "red_main_ttff_p90_minutes": 34.8,
            },
            "recorded_at": "2026-10-26T16:00:00Z",
        }
    }
    text = render_prometheus_text(snapshot)
    assert "rd_andon_backlog_total 12" in text
    assert "rd_andon_to_card_p90_minutes 185.50" in text
    assert "rd_andon_duplicate_fix_prs_total 16" in text
    assert "rd_andon_max_open_prs_per_issue 16" in text
    assert "rd_andon_unlinked_pr_share 0.9688" in text
    assert "rd_andon_red_main_ttff_p90_minutes 34.80" in text


def test_render_prometheus_text_omits_andon_when_cache_empty() -> None:
    text = render_prometheus_text({})
    assert "rd_andon_backlog_total" not in text
    assert "rd_andon_unlinked_pr_share" not in text


def test_collect_metrics_snapshot_reads_andon_metrics_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import cache_utils  # noqa: PLC0415
    from staff import fleet as staff_fleet  # noqa: PLC0415

    cached_andon = {
        "ok": True,
        "metrics": {
            "andon_backlog": 2,
            "andon_to_card_p90_minutes": 15.0,
            "duplicate_fix_prs_total": 0,
            "open_prs_per_issue_max": 1,
            "unlinked_pr_share": 0.0,
            "red_main_ttff_p90_minutes": 12.0,
        },
        "recorded_at": "2026-10-26T16:00:00Z",
    }

    def fake_cache_get(key: str, max_age_seconds: float = 3600.0) -> Any:
        return cached_andon if key == "andon_metrics" else {}

    monkeypatch.setattr(cache_utils, "cache_set", lambda k, v: None)
    monkeypatch.setattr(cache_utils, "cache_get", fake_cache_get)
    monkeypatch.setattr(staff_fleet, "local_board", lambda runner: {})
    snap = collect_metrics_snapshot()
    assert snap["andon"] == cached_andon
