#!/usr/bin/env python3
"""Andon and claim-gate metrics exporter (#2904 / epic #2900).

Computes epic #2900 acceptance metrics over recorded fixtures or live GitHub
datasets and publishes snapshots to the hub cache using the same DRY pattern as
``scripts.ci_slo_check`` under ``HUB_CACHE_KEY = "andon_metrics"``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.ci_slo_check import (  # noqa: E402
    DEFAULT_REPO,
    CommandRunner,
    _default_runner,
    _parse_ts,
    gh_api,
    gh_error,
    percentile,
)
from scripts.pr_claim_inventory import (  # noqa: E402
    extract_claim_agent,
    extract_fingerprint,
    extract_linked_issue,
)

HUB_CACHE_KEY = "andon_metrics"


@dataclass(frozen=True)
class AndonTargets:
    """Epic #2900 acceptance thresholds."""

    open_prs_per_issue_max: int = 1
    duplicate_fix_prs_per_fingerprint_max: int = 0
    unlinked_pr_share_max: float = 0.0
    open_andons_per_fingerprint_max: int = 1
    andon_to_card_p90_max_hours: float = 2.0


DEFAULT_TARGETS = AndonTargets()


@dataclass(frozen=True)
class AndonMetrics:
    """Computed epic #2900 acceptance and baseline metrics."""

    andon_backlog: int
    andon_max_age_hours: float
    andon_age_p90_hours: float
    andon_to_card_p90_hours: float
    andon_to_card_p90_minutes: float
    open_andons_per_fingerprint_max: int
    duplicate_fix_prs_per_fingerprint_max: int
    duplicate_fix_prs_total: int
    open_prs_per_issue_max: int
    open_prs_total: int
    cursor_open_prs: int
    claimed_pr_count: int
    unlinked_pr_share: float
    unlinked_pr_pct: float
    red_main_ttff_p90_minutes: float
    red_main_ttff_mean_minutes: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AndonDataset:
    """Input dataset for computing andon and claim-gate metrics."""

    as_of: datetime
    open_andons: list[dict[str, Any]]
    open_prs: list[dict[str, Any]]
    red_main_incidents: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> AndonDataset:
        """Validate and construct an AndonDataset from a parsed JSON mapping (DbC)."""
        if not isinstance(raw, Mapping):
            raise ValueError("dataset must be a mapping")
        for required_key in ("as_of", "open_andons", "open_prs"):
            if required_key not in raw:
                raise ValueError(f"dataset missing required key: {required_key}")

        as_of = _parse_ts(str(raw.get("as_of") or ""))
        raw_andons = raw.get("open_andons")
        if not isinstance(raw_andons, Sequence) or isinstance(raw_andons, (str, bytes)):
            raise ValueError("open_andons must be a list of mappings")
        open_andons: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_andons):
            if not isinstance(item, Mapping):
                raise ValueError(f"open_andons[{idx}] must be a mapping")
            if "number" not in item and "id" not in item:
                raise ValueError(f"open_andons[{idx}] missing required 'number' or 'id'")
            if not item.get("created_at"):
                raise ValueError(f"open_andons[{idx}] missing required 'created_at'")
            _parse_ts(str(item["created_at"]))
            if item.get("card_created_at"):
                _parse_ts(str(item["card_created_at"]))
            open_andons.append(dict(item))

        raw_prs = raw.get("open_prs")
        if not isinstance(raw_prs, Sequence) or isinstance(raw_prs, (str, bytes)):
            raise ValueError("open_prs must be a list of mappings")
        open_prs: list[dict[str, Any]] = []
        for idx, pr in enumerate(raw_prs):
            if not isinstance(pr, Mapping) or "number" not in pr:
                raise ValueError(f"open_prs[{idx}] must be a mapping with 'number'")
            open_prs.append(dict(pr))

        raw_incidents = raw.get("red_main_incidents") or []
        if not isinstance(raw_incidents, Sequence) or isinstance(raw_incidents, (str, bytes)):
            raise ValueError("red_main_incidents must be a list of mappings")
        incidents: list[dict[str, Any]] = []
        for idx, inc in enumerate(raw_incidents):
            if not isinstance(inc, Mapping):
                raise ValueError(f"red_main_incidents[{idx}] must be a mapping")
            detected = inc.get("detected_at") or inc.get("red_at")
            first_fix = inc.get("first_fix_pr_at")
            if not detected or not first_fix:
                raise ValueError(f"red_main_incidents[{idx}] requires 'detected_at' and 'first_fix_pr_at'")
            _parse_ts(str(detected))
            _parse_ts(str(first_fix))
            incidents.append(dict(inc))

        return cls(
            as_of=as_of,
            open_andons=open_andons,
            open_prs=open_prs,
            red_main_incidents=incidents,
        )


def _is_pr_linked_to_claimed_card(pr: Mapping[str, Any]) -> bool:
    linked_issue = extract_linked_issue(pr)
    if linked_issue is None:
        return False
    if bool(pr.get("claimed")):
        return True
    if pr.get("claim_agent") or extract_claim_agent(pr):
        return True
    return False


def _is_cursor_pr(pr: Mapping[str, Any]) -> bool:
    author = pr.get("author")
    if isinstance(author, Mapping):
        login = str(author.get("login") or "").lower()
    else:
        login = str(author or "").lower()
    head_ref = str(pr.get("headRefName") or pr.get("head_ref_name") or "").lower()
    return "cursor" in login or head_ref.startswith("cursor/")


def compute_andon_metrics(dataset: AndonDataset | Mapping[str, Any]) -> AndonMetrics:
    """Compute all epic #2900 acceptance metrics from an AndonDataset (DbC)."""
    ds = AndonDataset.from_mapping(dataset) if isinstance(dataset, Mapping) else dataset
    if not isinstance(ds, AndonDataset):
        raise ValueError("dataset must be an AndonDataset or mapping")

    ages_hours: list[float] = []
    card_latencies_hours: list[float] = []
    andon_fp_counts: Counter[str] = Counter()

    for andon in ds.open_andons:
        created = _parse_ts(str(andon["created_at"]))
        age_h = max(0.0, (ds.as_of - created).total_seconds() / 3600.0)
        ages_hours.append(age_h)
        if andon.get("card_created_at"):
            card_ts = _parse_ts(str(andon["card_created_at"]))
            card_latencies_hours.append(max(0.0, (card_ts - created).total_seconds() / 3600.0))
        fp = str(andon.get("fingerprint") or "").strip().lower()
        if fp:
            andon_fp_counts[fp] += 1

    issue_pr_counts: Counter[int] = Counter()
    pr_fp_counts: Counter[str] = Counter()
    claimed_count = 0
    cursor_count = 0

    for pr in ds.open_prs:
        issue_no = extract_linked_issue(pr)
        if issue_no is not None:
            issue_pr_counts[issue_no] += 1
        fp = extract_fingerprint(pr)
        if fp:
            pr_fp_counts[fp] += 1
        if _is_pr_linked_to_claimed_card(pr):
            claimed_count += 1
        if _is_cursor_pr(pr):
            cursor_count += 1

    total_prs = len(ds.open_prs)
    unlinked_count = max(0, total_prs - claimed_count)
    unlinked_share = (unlinked_count / total_prs) if total_prs > 0 else 0.0

    dup_per_fp = [max(0, count - 1) for count in pr_fp_counts.values()]
    dup_max = max(dup_per_fp, default=0)
    dup_total = sum(dup_per_fp)

    ttff_minutes: list[float] = []
    for inc in ds.red_main_incidents:
        detected = _parse_ts(str(inc.get("detected_at") or inc.get("red_at")))
        first_fix = _parse_ts(str(inc["first_fix_pr_at"]))
        ttff_minutes.append(max(0.0, (first_fix - detected).total_seconds() / 60.0))

    card_p90_hours = percentile(card_latencies_hours, 90.0)
    ttff_mean = (sum(ttff_minutes) / len(ttff_minutes)) if ttff_minutes else 0.0

    result = AndonMetrics(
        andon_backlog=len(ds.open_andons),
        andon_max_age_hours=max(ages_hours, default=0.0),
        andon_age_p90_hours=percentile(ages_hours, 90.0),
        andon_to_card_p90_hours=card_p90_hours,
        andon_to_card_p90_minutes=card_p90_hours * 60.0,
        open_andons_per_fingerprint_max=max(andon_fp_counts.values(), default=0),
        duplicate_fix_prs_per_fingerprint_max=dup_max,
        duplicate_fix_prs_total=dup_total,
        open_prs_per_issue_max=max(issue_pr_counts.values(), default=0),
        open_prs_total=total_prs,
        cursor_open_prs=cursor_count,
        claimed_pr_count=claimed_count,
        unlinked_pr_share=unlinked_share,
        unlinked_pr_pct=unlinked_share * 100.0,
        red_main_ttff_p90_minutes=percentile(ttff_minutes, 90.0),
        red_main_ttff_mean_minutes=ttff_mean,
    )
    assert 0.0 <= result.unlinked_pr_share <= 1.0, "postcondition: unlinked_pr_share in [0, 1]"
    assert result.andon_backlog >= 0, "postcondition: non-negative backlog"
    return result


def evaluate_breaches(
    metrics: AndonMetrics,
    *,
    targets: AndonTargets | None = None,
) -> list[str]:
    """Compare computed metrics against epic #2900 targets and return breach descriptions."""
    t = targets or DEFAULT_TARGETS
    breaches: list[str] = []
    if metrics.open_prs_per_issue_max > t.open_prs_per_issue_max:
        breaches.append(f"open PRs per issue max {metrics.open_prs_per_issue_max} > {t.open_prs_per_issue_max}")
    if metrics.duplicate_fix_prs_per_fingerprint_max > t.duplicate_fix_prs_per_fingerprint_max:
        breaches.append(
            f"duplicate fix PRs per fingerprint max {metrics.duplicate_fix_prs_per_fingerprint_max} "
            f"> {t.duplicate_fix_prs_per_fingerprint_max}"
        )
    if metrics.unlinked_pr_share > t.unlinked_pr_share_max:
        breaches.append(f"unlinked PR share {metrics.unlinked_pr_pct:.2f}% > {t.unlinked_pr_share_max * 100:.2f}%")
    if metrics.open_andons_per_fingerprint_max > t.open_andons_per_fingerprint_max:
        breaches.append(
            f"open andons per fingerprint max {metrics.open_andons_per_fingerprint_max} "
            f"> {t.open_andons_per_fingerprint_max}"
        )
    if metrics.andon_to_card_p90_hours > t.andon_to_card_p90_max_hours:
        breaches.append(
            f"andon-to-card p90 {metrics.andon_to_card_p90_hours:.2f}h > {t.andon_to_card_p90_max_hours:.2f}h"
        )
    return breaches


def build_hub_snapshot(
    metrics: AndonMetrics,
    *,
    ok: bool = True,
    detail: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Build the hub cache snapshot payload mirroring scripts.ci_slo_check (DbC)."""
    ts = recorded_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "ok": bool(ok),
        "metrics": asdict(metrics),
        "detail": dict(detail or {}),
        "recorded_at": ts,
    }
    assert payload.get("recorded_at"), "postcondition: recorded_at required"
    assert isinstance(payload.get("metrics"), dict), "postcondition: metrics dict required"
    return payload


def publish_hub_snapshot(
    metrics: AndonMetrics,
    *,
    ok: bool = True,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish the snapshot to cache_utils under HUB_CACHE_KEY; surface failures explicitly."""
    payload = build_hub_snapshot(metrics, ok=ok, detail=detail)
    try:
        from cache_utils import cache_set  # noqa: PLC0415

        cache_set(HUB_CACHE_KEY, payload)
    except Exception as exc:  # noqa: BLE001
        gh_error(f"Failed to publish {HUB_CACHE_KEY} snapshot: {exc}")
        raise RuntimeError(f"Failed to publish {HUB_CACHE_KEY} snapshot: {exc}") from exc
    return payload


def fetch_live_dataset(
    repo: str,
    *,
    runner: CommandRunner = _default_runner,
) -> AndonDataset:
    """Fetch open andon issues and open PRs from GitHub via read-only gh_api."""
    now = datetime.now(UTC)
    owner, name = repo.split("/", 1)
    raw_issues = gh_api(
        f"repos/{owner}/{name}/issues",
        runner=runner,
        params={"state": "open", "labels": "andon", "per_page": "100"},
    )
    open_andons: list[dict[str, Any]] = []
    for item in raw_issues if isinstance(raw_issues, list) else []:
        if not isinstance(item, Mapping) or "pull_request" in item:
            continue
        open_andons.append(
            {
                "number": int(item.get("number") or 0),
                "created_at": str(item.get("created_at") or now.isoformat().replace("+00:00", "Z")),
                "fingerprint": extract_fingerprint(item),
            }
        )

    raw_prs = gh_api(
        f"repos/{owner}/{name}/pulls",
        runner=runner,
        params={"state": "open", "per_page": "100"},
    )
    open_prs: list[dict[str, Any]] = []
    for pr in raw_prs if isinstance(raw_prs, list) else []:
        if not isinstance(pr, Mapping):
            continue
        open_prs.append(
            {
                "number": int(pr.get("number") or 0),
                "title": str(pr.get("title") or ""),
                "body": str(pr.get("body") or ""),
                "headRefName": str((pr.get("head") or {}).get("ref") or pr.get("headRefName") or ""),
                "author": (pr.get("user") or {}).get("login") or pr.get("author"),
                "labels": pr.get("labels") or [],
            }
        )
    return AndonDataset(as_of=now, open_andons=open_andons, open_prs=open_prs)


def run_check(
    dataset: AndonDataset,
    *,
    targets: AndonTargets | None = None,
    publish: bool = False,
    enforce: bool = False,
) -> int:
    """Compute metrics, print report JSON, optionally publish to hub, and return exit code."""
    metrics = compute_andon_metrics(dataset)
    breaches = evaluate_breaches(metrics, targets=targets)
    report: dict[str, Any] = {
        "metrics": asdict(metrics),
        "breaches": breaches,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if publish:
        publish_hub_snapshot(metrics, ok=not breaches, detail=report)
    if enforce and breaches:
        for item in breaches:
            gh_error(f"Andon SLO breach: {item}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for andon and claim-gate metrics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--fixture", type=Path, default=None, help="Path to recorded dataset JSON fixture.")
    parser.add_argument("--publish-hub", action="store_true")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.fixture is not None:
        raw = json.loads(args.fixture.read_text(encoding="utf-8"))
        dataset = AndonDataset.from_mapping(raw)
    else:
        dataset = fetch_live_dataset(args.repo)
    return run_check(dataset, publish=args.publish_hub, enforce=args.enforce)


if __name__ == "__main__":
    raise SystemExit(main())
