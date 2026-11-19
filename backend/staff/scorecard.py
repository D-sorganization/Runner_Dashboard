"""Daily per-model scorecard (#4564, scope item 2): how did each model actually do, by day?

``daily_scorecard`` is pure, like :func:`staff.outcomes.aggregate`: run records plus
per-PR facts in, day-and-model rows out, with no I/O of its own. It reads the same PR
facts the outcome scorecard already fetches, so a day's merge time, CI failure rate and
review findings cost no extra GitHub traffic.

Bucketing is by UTC calendar day: run stamps, provider quotas and the fleet's daily
rhythms are all kept in UTC, and a local-midnight bucket would move a run between days
depending on which machine asks. A run that recorded no model is reported under
:data:`UNKNOWN_MODEL`, the label ``delivery_metrics.attribution`` already gives an
unattributable PR (``report_cards`` calls the same population ``default``; merging the
two spellings here would invent a third).

A measure whose denominator holds no data is ``None``, never ``0.0``: an empty day is
unmeasured, not a perfect score.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from staff.outcomes import PrFact, PrKey, pr_key, rate
from staff.store import RunRecord
from time_utils import parse_github_ts

from staff import outcomes

log = logging.getLogger("dashboard.staff.scorecard")

#: Model label for a run that recorded no model of its own (#2047 provider fallbacks leave it empty).
UNKNOWN_MODEL = "unknown"

#: The window a scorecard covers when the caller asks for none: the same span ``/outcomes`` defaults to.
DEFAULT_DAYS = outcomes.DEFAULT_WINDOW.days
MAX_DAYS = 90


def _z(stamp: datetime) -> str:
    return stamp.isoformat().replace("+00:00", "Z")


def window(now: datetime | None = None, *, days: int = DEFAULT_DAYS) -> tuple[str, str]:
    """The ``(since, until)`` stamps of the last ``days`` UTC days, oldest bound at midnight.

    Pre: ``1 <= days <= MAX_DAYS``; ``now`` is aware (UTC is assumed when it is not).
    """
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}, got {days}")
    reference = now if now is not None else datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    start = (reference - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return _z(start), _z(reference.replace(microsecond=0))


def utc_day(created_at: str) -> str | None:
    """The UTC calendar day (``YYYY-MM-DD``) of a run stamp, or None when it cannot be read."""
    try:
        stamp = parse_github_ts(created_at or "")
    except ValueError:
        return None
    return None if stamp is None else stamp.strftime("%Y-%m-%d")


def model_of(rec: RunRecord) -> str:
    """The model one run actually used, or :data:`UNKNOWN_MODEL` when it recorded none."""
    return (rec.model or "").strip() or UNKNOWN_MODEL


def _metrics(runs: Sequence[RunRecord], facts: Mapping[PrKey, PrFact]) -> dict[str, Any]:
    """The scorecard measures for one bucket. Pure: run counts plus PR facts, no I/O.

    ``merge_seconds_median`` counts only merged PRs that yielded a duration, and
    ``findings_per_merged_pr`` divides by the merged PRs that reported a findings count, so
    a PR whose facts could not be read lowers neither.
    """
    keys = {key for key in (pr_key(rec) for rec in runs) if key is not None}
    known = [facts[key] for key in keys if key in facts]
    merged = [fact for fact in known if fact.state == "merged"]
    merge_times = [fact.merge_seconds for fact in merged if fact.merge_seconds is not None]
    ci_known = [fact.ci_first_pass for fact in known if fact.ci_first_pass is not None]
    ci_failed = sum(1 for passed in ci_known if not passed)
    reported = [fact.review_findings for fact in known if fact.review_findings is not None]
    merged_reported = [fact.review_findings for fact in merged if fact.review_findings is not None]
    return {
        "runs": len(runs),
        "succeeded": sum(rec.status == "succeeded" for rec in runs),
        "verified": sum(rec.verification == "verified" for rec in runs),
        "failed_verification": sum(rec.verification == "failed" for rec in runs),
        "cost_usd": round(sum(rec.cost_usd or 0.0 for rec in runs), 4),
        "prs": len(keys),
        "prs_unknown": len(keys) - len(known),
        "merged": len(merged),
        "closed_unmerged": sum(fact.state == "closed_unmerged" for fact in known),
        "open": sum(fact.state == "open" for fact in known),
        "ci_known": len(ci_known),
        "ci_failed": ci_failed,
        "ci_failure_rate": rate(ci_failed, len(ci_known)),
        "merge_seconds_known": len(merge_times),
        "merge_seconds_median": round(float(median(merge_times)), 1) if merge_times else None,
        "review_findings": sum(reported) if reported else None,
        "findings_per_merged_pr": rate(sum(merged_reported), len(merged_reported)),
    }


def daily_scorecard(
    runs: Sequence[RunRecord],
    facts: Mapping[PrKey, PrFact],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Rows per UTC day and model, a per-model roll-up over the window, and the totals.

    Pre: ``facts`` is keyed as in :func:`staff.outcomes.aggregate`. Pure: no I/O.
    Post: ``rows`` is sorted by date then model and ``models`` by model; every entry carries
    the measure keys of ``totals``, and each ``models`` entry adds the UTC days it measured.
    """
    by_day_model: dict[tuple[str, str], list[RunRecord]] = {}
    by_model: dict[str, list[RunRecord]] = {}
    days_per_model: dict[str, set[str]] = {}
    bucketed: list[RunRecord] = []
    for rec in runs:
        label = model_of(rec)
        if model is not None and label != model:
            continue
        day = utc_day(rec.created_at)
        if day is None:
            log.info("scorecard: run %s has unreadable created_at %r, excluded", rec.id, rec.created_at)
            continue
        by_day_model.setdefault((day, label), []).append(rec)
        by_model.setdefault(label, []).append(rec)
        days_per_model.setdefault(label, set()).add(day)
        bucketed.append(rec)

    rows = [
        {"date": day, "model": label, **_metrics(group, facts)} for (day, label), group in sorted(by_day_model.items())
    ]
    models = [
        {"model": label, "days": len(days_per_model[label]), **_metrics(group, facts)}
        for label, group in sorted(by_model.items())
    ]
    return {"rows": rows, "models": models, "totals": _metrics(bucketed, facts)}
