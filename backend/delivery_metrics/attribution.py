"""Map staff runs to PR attribution and dimension groupings (#2117 gap 3)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from delivery_metrics.models import PrSnapshot
from delivery_metrics.rollup import _median
from staff.store import RunRecord, get_run_store

_DEFAULT_ORG = "D-sorganization"


@dataclass(frozen=True)
class PrAttribution:
    role: str
    agent: str
    model: str


def repo_short_name(repo_full: str) -> str:
    return repo_full.split("/")[-1]


def load_attribution_runs(pulls: Sequence[PrSnapshot]) -> list[RunRecord]:
    """Staff runs for PRs in the metrics window (includes retries; no created_at cutoff)."""
    keys = [(repo_short_name(p.repo_full), p.number) for p in pulls]
    return get_run_store().list_runs_for_prs(keys, logical_only=False)


def normalize_repo_full(repo: str, *, org: str = _DEFAULT_ORG) -> str:
    if "/" in repo:
        return repo
    return f"{org}/{repo}"


def build_attribution_index(
    runs: Sequence[RunRecord],
    *,
    org: str = _DEFAULT_ORG,
) -> dict[tuple[str, int], PrAttribution]:
    """Latest staff run per ``(repo_full, pr_number)`` wins."""
    ordered = sorted(
        (r for r in runs if r.pr_number is not None),
        key=lambda r: r.created_at,
    )
    index: dict[tuple[str, int], PrAttribution] = {}
    for run in ordered:
        if run.pr_number is None:
            continue
        repo_full = normalize_repo_full(run.repo, org=org)
        key = (repo_full, int(run.pr_number))
        index[key] = PrAttribution(
            role=run.role,
            agent=run.provider,
            model=run.model or "unknown",
        )
    return index


def attach_attribution(row: Mapping[str, Any], attr: PrAttribution | None) -> dict[str, Any]:
    out = dict(row)
    if attr is None:
        out.update({"role": None, "agent": None, "model": None})
    else:
        out.update({"role": attr.role, "agent": attr.agent, "model": attr.model})
    return out


def _bucket_rows(rows: Sequence[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        raw = row.get(field)
        key = str(raw) if raw else "unattributed"
        buckets[key].append(row)
    out: list[dict[str, Any]] = []
    for key in sorted(buckets):
        items = buckets[key]
        ci = [int(r["ci_rounds_to_green"]) for r in items if r.get("ci_rounds_to_green") is not None]
        out.append(
            {
                "key": key,
                "prs": len(items),
                "merged": sum(1 for r in items if r.get("merged")),
                "median_ci_rounds": _median(ci),
            }
        )
    return out


def dimension_groups(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Weekly-style buckets by role, agent (provider), and model — not rankings."""
    return {
        "by_role": _bucket_rows(rows, "role"),
        "by_agent": _bucket_rows(rows, "agent"),
        "by_model": _bucket_rows(rows, "model"),
    }
