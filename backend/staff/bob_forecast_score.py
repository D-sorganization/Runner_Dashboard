"""Bob's wave-scoring (RD#3642): forecasted lane/issue items from a Barb-style
markdown table, scored against the fleet repos' actual merges in a window."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

_LANE_ROW = re.compile(r"^\|\s*(?P<lane>[^|]+?)\s*\|\s*(?P<item>[^|]+?)\s*\|\s*$")
_ISSUE_REF = re.compile(
    r"\b(?P<prefix>RD|RM|GM|UD|AD)\s*#(?P<num>\d+)\b|(?<!\w)#(?P<plain>\d+)\b",
    re.IGNORECASE,
)
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")

_ORG = "D-sorganization"

REPO_ALIASES: Mapping[str, str] = {
    "RD": f"{_ORG}/Runner_Dashboard",
    "RM": f"{_ORG}/Repository_Management",
    "GM": f"{_ORG}/Gasification_Model",
    "UD": f"{_ORG}/UpstreamDrift",
    "AD": f"{_ORG}/AffineDrift",
}


@dataclass(frozen=True)
class ForecastItem:
    lane: str
    repo: str
    issue_number: int
    raw_text: str


@dataclass(frozen=True)
class MergedPr:
    repo: str
    number: int
    merged_at: datetime
    closes_issues: tuple[int, ...]


@dataclass(frozen=True)
class ForecastVerdict:
    item: ForecastItem
    verdict: str  # "hit" | "miss" | "pending"
    merged_pr_number: int | None


def _is_separator_row(lane: str, item: str) -> bool:
    cells = (lane, item)
    return all(_SEPARATOR_CELL.match(cell) for cell in cells)


def parse_forecast_table(text: str, *, default_repo: str) -> list[ForecastItem]:
    """Parse `| lane | item (#N, RD #N, ...) |` rows. Rows with no issue ref, the
    header row, and separator rows are skipped. A row naming several issues
    yields one ForecastItem per issue."""
    items: list[ForecastItem] = []
    for raw_line in text.splitlines():
        match = _LANE_ROW.match(raw_line.strip())
        if not match:
            continue
        lane, item_text = match.group("lane"), match.group("item")
        if lane.lower() == "lane" or _is_separator_row(lane, item_text):
            continue
        for ref in _ISSUE_REF.finditer(item_text):
            if ref.group("prefix"):
                repo = REPO_ALIASES.get(ref.group("prefix").upper(), default_repo)
                num = int(ref.group("num"))
            else:
                repo, num = default_repo, int(ref.group("plain"))
            items.append(ForecastItem(lane=lane, repo=repo, issue_number=num, raw_text=item_text))
    return items


def score_forecast(
    items: Sequence[ForecastItem],
    merged_prs: Sequence[MergedPr],
    *,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
) -> list[ForecastVerdict]:
    """Score each item: ``hit`` if a PR closing its issue merged in
    ``[window_start, window_end)``, ``miss`` if the window has already closed
    with no such merge, else ``pending`` (too early to call)."""
    if window_start >= window_end:
        raise ValueError("window_start must precede window_end")
    hit_by_issue: dict[tuple[str, int], MergedPr] = {}
    for pr in merged_prs:
        if not (window_start <= pr.merged_at < window_end):
            continue
        for num in pr.closes_issues:
            hit_by_issue.setdefault((pr.repo, num), pr)
    window_closed = now >= window_end
    verdicts: list[ForecastVerdict] = []
    for item in items:
        hit = hit_by_issue.get((item.repo, item.issue_number))
        if hit is not None:
            verdict = "hit"
        elif window_closed:
            verdict = "miss"
        else:
            verdict = "pending"
        verdicts.append(ForecastVerdict(item=item, verdict=verdict, merged_pr_number=hit.number if hit else None))
    return verdicts


def build_merged_prs_query(repos: Sequence[str], *, since: datetime, until: datetime, first: int = 50) -> str:
    """One GraphQL query with a `search(...)` alias per repo, each scoped to
    merged PRs in `[since, until)`. `closingIssuesReferences` is what maps a
    merged PR back to the issue(s) it was forecasted against."""
    if not repos:
        raise ValueError("repos must not be empty")
    window = f"{since.isoformat()}..{until.isoformat()}"
    fields = "nodes { ... on PullRequest { number mergedAt closingIssuesReferences(first: 10) { nodes { number } } } }"
    aliases = []
    for index, repo in enumerate(repos):
        search_query = f"repo:{repo} is:pr is:merged merged:{window}"
        aliases.append(f's{index}: search(query: "{search_query}", type: ISSUE, first: {first}) {{ {fields} }}')
    return "query { " + " ".join(aliases) + " }"


def parse_merged_prs_response(payload: Mapping[str, object], repos: Sequence[str]) -> list[MergedPr]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("missing GraphQL data")
    prs: list[MergedPr] = []
    for index, repo in enumerate(repos):
        search_result = data.get(f"s{index}")
        nodes = search_result.get("nodes") if isinstance(search_result, Mapping) else None
        if not isinstance(nodes, list):
            raise ValueError(f"malformed search result for {repo}")
        for node in nodes:
            prs.append(_normalize_merged_pr(repo, node))
    return prs


def _normalize_merged_pr(repo: str, node: object) -> MergedPr:
    if not isinstance(node, Mapping) or not node:
        raise ValueError(f"malformed PR node for {repo}")
    number, merged_at_raw = node.get("number"), node.get("mergedAt")
    if type(number) is not int or number <= 0:
        raise ValueError(f"invalid PR number for {repo}")
    if not isinstance(merged_at_raw, str) or not merged_at_raw:
        raise ValueError(f"invalid mergedAt for {repo}#{number}")
    merged_at = datetime.fromisoformat(merged_at_raw.replace("Z", "+00:00"))
    closing_refs = node.get("closingIssuesReferences")
    closing_nodes = closing_refs.get("nodes") if isinstance(closing_refs, Mapping) else None
    closes = tuple(
        n["number"] for n in (closing_nodes or []) if isinstance(n, Mapping) and type(n.get("number")) is int
    )
    return MergedPr(repo=repo, number=number, merged_at=merged_at, closes_issues=closes)


def summarize(verdicts: Sequence[ForecastVerdict]) -> dict[str, object]:
    by_lane: dict[str, dict[str, int]] = {}
    for v in verdicts:
        bucket = by_lane.setdefault(v.item.lane, {"hit": 0, "miss": 0, "pending": 0})
        bucket[v.verdict] += 1
    scored = sum(1 for v in verdicts if v.verdict != "pending")
    hits = sum(1 for v in verdicts if v.verdict == "hit")
    return {
        "total": len(verdicts),
        "hits": hits,
        "misses": sum(1 for v in verdicts if v.verdict == "miss"),
        "pending": sum(1 for v in verdicts if v.verdict == "pending"),
        "hit_rate": round(hits / scored, 4) if scored else None,
        "by_lane": by_lane,
    }
