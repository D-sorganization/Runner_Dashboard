#!/usr/bin/env python3
"""Andon and claim-gate SLO metrics exporter (#2904, epic #2900)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "backend")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from scripts.ci_slo_check import _parse_ts, gh_error, percentile  # noqa: E402

HUB_CACHE_KEY = "andon_metrics"


@dataclass(frozen=True)
class AndonTargets:
    """Acceptance thresholds from epic #2900."""

    max_open_prs_per_issue: int = 1
    max_duplicate_fix_prs: int = 0
    max_unlinked_pr_share: float = 0.0
    max_duplicate_open_andons: int = 0
    andon_to_card_p90_max_minutes: float = 120.0
    red_main_ttff_p90_max_minutes: float = 60.0


DEFAULT_TARGETS = AndonTargets()


@dataclass(frozen=True)
class AndonMetrics:
    """Computed andon and claim-gate metrics matching epic #2900."""

    andon_backlog: int
    andon_max_age_minutes: float
    andon_age_p90_minutes: float
    andon_to_card_p90_minutes: float
    duplicate_fix_prs: int
    max_prs_per_fingerprint: int
    duplicate_open_andons: int
    max_open_prs_per_issue: int
    open_prs_per_issue_mean: float
    open_pr_count: int
    unlinked_pr_count: int
    unlinked_pr_share: float
    red_main_ttff_p90_minutes: float

    def __getitem__(self, key: str) -> Any:
        return asdict(self)[key]

    def get(self, key: str, default: Any = None) -> Any:
        return asdict(self).get(key, default)


@dataclass
class AndonDataset:
    """Validated input dataset for andon and claim-gate metrics."""

    as_of: datetime
    open_andons: list[dict[str, Any]]
    open_prs: list[dict[str, Any]]
    red_main_incidents: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> AndonDataset:
        if not isinstance(raw, Mapping) or not {"as_of", "open_andons", "open_prs"} <= set(raw):
            raise ValueError("dataset mapping must include 'as_of', 'open_andons', and 'open_prs'")
        as_of = _parse_ts(str(raw.get("as_of") or ""))
        open_andons_raw, open_prs_raw = raw.get("open_andons"), raw.get("open_prs")
        red_main_raw = raw.get("red_main_incidents", [])
        if not isinstance(open_andons_raw, list) or not isinstance(open_prs_raw, list):
            raise ValueError("'open_andons' and 'open_prs' must be lists")
        if not isinstance(red_main_raw, list):
            raise ValueError("'red_main_incidents' must be a list")

        open_andons: list[dict[str, Any]] = []
        for idx, item in enumerate(open_andons_raw):
            if not isinstance(item, Mapping) or "number" not in item or "opened_at" not in item:
                raise ValueError(f"open_andons[{idx}] requires 'number' and 'opened_at'")
            _parse_ts(str(item["opened_at"]))
            if item.get("card_created_at") is not None:
                _parse_ts(str(item["card_created_at"]))
            open_andons.append(dict(item))

        open_prs: list[dict[str, Any]] = []
        for idx, item in enumerate(open_prs_raw):
            if not isinstance(item, Mapping) or "number" not in item:
                raise ValueError(f"open_prs[{idx}] requires 'number'")
            open_prs.append(dict(item))

        red_main_incidents: list[dict[str, Any]] = []
        for idx, item in enumerate(red_main_raw):
            if not isinstance(item, Mapping) or "detected_at" not in item or "first_fix_pr_at" not in item:
                raise ValueError(f"red_main_incidents[{idx}] requires 'detected_at' and 'first_fix_pr_at'")
            _parse_ts(str(item["detected_at"]))
            _parse_ts(str(item["first_fix_pr_at"]))
            red_main_incidents.append(dict(item))

        return cls(as_of=as_of, open_andons=open_andons, open_prs=open_prs, red_main_incidents=red_main_incidents)


def _is_pr_linked_to_claimed_card(pr: Mapping[str, Any]) -> bool:
    if "linked_claimed_card" in pr:
        return bool(pr["linked_claimed_card"])
    return bool(pr.get("issue_number") and pr.get("has_valid_claim"))


def compute_andon_metrics(dataset: AndonDataset | Mapping[str, Any]) -> AndonMetrics:
    """Compute all epic #2900 acceptance metrics from a validated dataset."""
    ds = dataset if isinstance(dataset, AndonDataset) else AndonDataset.from_mapping(dataset)
    ages: list[float] = []
    card_latencies: list[float] = []
    andon_fp_counts: Counter[str] = Counter()

    for andon in ds.open_andons:
        opened = _parse_ts(str(andon["opened_at"]))
        ages.append(max(0.0, (ds.as_of - opened).total_seconds() / 60.0))
        if andon.get("card_created_at"):
            card_created = _parse_ts(str(andon["card_created_at"]))
            card_latencies.append(max(0.0, (card_created - opened).total_seconds() / 60.0))
        if fp := str(andon.get("fingerprint") or "").strip():
            andon_fp_counts[fp] += 1

    pr_fp_counts: Counter[str] = Counter()
    pr_issue_counts: Counter[int] = Counter()
    unlinked_count = 0
    for pr in ds.open_prs:
        if not _is_pr_linked_to_claimed_card(pr):
            unlinked_count += 1
        if fp := str(pr.get("fingerprint") or "").strip():
            pr_fp_counts[fp] += 1
        if (issue_no := pr.get("issue_number")) is not None:
            pr_issue_counts[int(issue_no)] += 1

    open_pr_count = len(ds.open_prs)
    ttff_minutes = [
        max(0.0, (_parse_ts(str(inc["first_fix_pr_at"])) - _parse_ts(str(inc["detected_at"]))).total_seconds() / 60.0)
        for inc in ds.red_main_incidents
    ]
    result = AndonMetrics(
        andon_backlog=len(ds.open_andons),
        andon_max_age_minutes=max(ages, default=0.0),
        andon_age_p90_minutes=percentile(ages, 90.0),
        andon_to_card_p90_minutes=percentile(card_latencies, 90.0),
        duplicate_fix_prs=sum(max(0, c - 1) for c in pr_fp_counts.values()),
        max_prs_per_fingerprint=max(pr_fp_counts.values(), default=0),
        duplicate_open_andons=sum(max(0, c - 1) for c in andon_fp_counts.values()),
        max_open_prs_per_issue=max(pr_issue_counts.values(), default=0),
        open_prs_per_issue_mean=(sum(pr_issue_counts.values()) / len(pr_issue_counts)) if pr_issue_counts else 0.0,
        open_pr_count=open_pr_count,
        unlinked_pr_count=unlinked_count,
        unlinked_pr_share=(unlinked_count / open_pr_count) if open_pr_count > 0 else 0.0,
        red_main_ttff_p90_minutes=percentile(ttff_minutes, 90.0),
    )
    assert 0.0 <= result.unlinked_pr_share <= 1.0, "unlinked_pr_share postcondition out of [0, 1]"
    return result


def evaluate_breaches(metrics: AndonMetrics, *, targets: AndonTargets | None = None) -> list[str]:
    """Return human-readable SLO breach descriptions for epic #2900 criteria."""
    t = targets or DEFAULT_TARGETS
    breaches: list[str] = []
    if metrics.max_open_prs_per_issue > t.max_open_prs_per_issue:
        breaches.append(f"max open PRs per issue {metrics.max_open_prs_per_issue} > {t.max_open_prs_per_issue}")
    if metrics.duplicate_fix_prs > t.max_duplicate_fix_prs:
        breaches.append(f"duplicate fix PRs {metrics.duplicate_fix_prs} > {t.max_duplicate_fix_prs}")
    if metrics.unlinked_pr_share > t.max_unlinked_pr_share:
        breaches.append(
            f"unlinked PR share {metrics.unlinked_pr_share * 100:.1f}% > {t.max_unlinked_pr_share * 100:.1f}%"
        )
    if metrics.duplicate_open_andons > t.max_duplicate_open_andons:
        breaches.append(f"duplicate open andons {metrics.duplicate_open_andons} > {t.max_duplicate_open_andons}")
    if metrics.andon_to_card_p90_minutes > t.andon_to_card_p90_max_minutes:
        breaches.append(
            f"andon-to-card p90 {metrics.andon_to_card_p90_minutes:.1f}m > {t.andon_to_card_p90_max_minutes:.1f}m"
        )
    if metrics.red_main_ttff_p90_minutes > t.red_main_ttff_p90_max_minutes:
        breaches.append(
            f"red-main TTFF p90 {metrics.red_main_ttff_p90_minutes:.1f}m > {t.red_main_ttff_p90_max_minutes:.1f}m"
        )
    return breaches


def build_hub_snapshot(
    metrics: AndonMetrics | Mapping[str, Any],
    *,
    ok: bool = True,
    detail: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Construct the hub cache snapshot dictionary with ``recorded_at``."""
    metrics_dict = asdict(metrics) if isinstance(metrics, AndonMetrics) else dict(metrics)
    if not metrics_dict:
        raise ValueError("metrics mapping cannot be empty")
    ts = recorded_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _parse_ts(ts)
    return {"ok": bool(ok), "metrics": metrics_dict, "detail": dict(detail or {}), "recorded_at": ts}


def publish_hub_snapshot(
    metrics: AndonMetrics | Mapping[str, Any],
    *,
    ok: bool = True,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish the andon metrics snapshot to hub cache via ``cache_utils.cache_set``."""
    payload = build_hub_snapshot(metrics, ok=ok, detail=detail)
    try:
        from cache_utils import cache_set  # noqa: PLC0415

        cache_set(HUB_CACHE_KEY, payload)
    except Exception as exc:
        gh_error(f"Failed to publish {HUB_CACHE_KEY} hub snapshot: {exc}")
        raise RuntimeError(f"Failed to publish {HUB_CACHE_KEY} hub snapshot: {exc}") from exc
    return payload


def run_check(
    dataset: AndonDataset | Mapping[str, Any],
    *,
    mode: Literal["full", "report"] = "full",
    targets: AndonTargets | None = None,
    publish: bool = False,
) -> int:
    """Evaluate andon SLOs, print JSON report, optionally publish, and return exit code."""
    metrics = compute_andon_metrics(dataset)
    breaches = evaluate_breaches(metrics, targets=targets)
    report: dict[str, Any] = {"metrics": asdict(metrics), "breaches": breaches}
    print(json.dumps(report, indent=2, sort_keys=True))
    if publish:
        publish_hub_snapshot(metrics, ok=not breaches, detail=report)
    if mode == "full" and breaches:
        for item in breaches:
            gh_error(f"Andon SLO breach: {item}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, help="Path to JSON dataset fixture")
    parser.add_argument("--mode", choices=("full", "report"), default="full")
    parser.add_argument("--publish-hub", action="store_true")
    args = parser.parse_args(argv)
    dataset = AndonDataset.from_mapping(json.loads(Path(args.fixture).read_text(encoding="utf-8")))
    return run_check(dataset, mode=args.mode, publish=args.publish_hub)


if __name__ == "__main__":
    raise SystemExit(main())
