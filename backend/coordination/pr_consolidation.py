"""Open-PR consolidation suggestions for overloaded repos (split from overload.py, #3880)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pr_inventory
from delivery_metrics.models import PrSnapshot

DEFAULT_PR_SIZE_CAP = 500  #: max combined changed lines (additions + deletions) per consolidated PR


def _normalize_branch_prefix(head_ref: str) -> str | None:
    """Derive a topic-scoped branch prefix for consolidation grouping."""
    slash_parts = [part.strip().lower() for part in head_ref.strip().split("/") if part.strip()]
    if not slash_parts:
        return None
    tokens = [tok for tok in slash_parts[-1].split("-") if tok]
    if len(tokens) < 2:
        return None
    if tokens[0].isdigit():
        return f"{tokens[0]}-{tokens[1]}"
    alpha_tokens = [tok for tok in tokens if tok.isalpha()]
    if len(alpha_tokens) < 2:
        return None
    stem = f"{alpha_tokens[0]}-{alpha_tokens[1]}"
    return f"{slash_parts[0]}/{stem}" if len(slash_parts) >= 2 else stem


def _normalize_open_pr_for_consolidation(
    raw: Mapping[str, Any], recent_by_number: Mapping[int, PrSnapshot]
) -> dict[str, Any] | None:
    number = raw.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
        return None
    if "changed_lines" in raw and raw["changed_lines"] is not None:
        changed_lines = int(raw["changed_lines"])
    elif "additions" in raw or "deletions" in raw:
        changed_lines = int(raw.get("additions") or 0) + int(raw.get("deletions") or 0)
    elif number in recent_by_number:
        changed_lines = int(recent_by_number[number].additions) + int(recent_by_number[number].deletions)
    else:
        changed_lines = 0
    if changed_lines < 0:
        raise ValueError(f"changed_lines must be non-negative for PR #{number}, got {changed_lines!r}")
    head_ref = str(raw.get("head_ref") or raw.get("headRefName") or raw.get("branch") or "").strip()
    raw_issues = raw.get("linked_issues")
    if isinstance(raw_issues, Sequence) and not isinstance(raw_issues, (str, bytes)):
        linked_issues = sorted({int(i) for i in raw_issues if isinstance(i, int) and not isinstance(i, bool)})
    else:
        linked_issues = pr_inventory._parse_linked_issues(raw.get("body"))
    raw_paths = raw.get("changed_paths") or raw.get("paths") or raw.get("files") or ()
    return {
        "number": number,
        "title": str(raw.get("title") or ""),
        "head_ref": head_ref,
        "branch_prefix": _normalize_branch_prefix(head_ref),
        "linked_issues": linked_issues,
        "changed_paths": sorted({str(p).strip() for p in raw_paths if isinstance(p, str) and str(p).strip()}),
        "changed_lines": changed_lines,
        "draft": bool(raw.get("draft", False)),
    }


def _build_consolidation_suggestion(
    group_by: str,
    group_key: str,
    surviving: dict[str, Any],
    absorbed: list[dict[str, Any]],
    skipped_over_cap: list[int],
    combined_lines: int,
    size_cap: int,
) -> dict[str, Any]:
    chosen = [surviving, *absorbed]
    shared_issues = sorted(
        {i for p in chosen for i in p["linked_issues"] if sum(i in q["linked_issues"] for q in chosen) >= 2}
    )
    shared_paths = sorted(
        {f for p in chosen for f in p["changed_paths"] if sum(f in q["changed_paths"] for q in chosen) >= 2}
    )
    shared_prefixes = sorted(
        {
            str(p["branch_prefix"])
            for p in chosen
            if p["branch_prefix"] and sum(q["branch_prefix"] == p["branch_prefix"] for q in chosen) >= 2
        }
    )
    absorbed_nums = [p["number"] for p in absorbed]
    return {
        "group_by": group_by,
        "group_key": group_key,
        "reasons": [f"linked_issue:#{i}" for i in shared_issues]
        + [f"branch_prefix:{p}" for p in shared_prefixes]
        + [f"overlapping_paths:{p}" for p in shared_paths],
        "surviving_pr": surviving["number"],
        "absorbed_prs": absorbed_nums,
        "skipped_over_cap_prs": skipped_over_cap,
        "combined_changed_lines": combined_lines,
        "size_cap": size_cap,
        "shared_issues": shared_issues,
        "shared_paths": shared_paths,
        "branch_prefix": shared_prefixes[0] if shared_prefixes else surviving["branch_prefix"],
        "auto_close": False,
        "action": (
            f"Keep #{surviving['number']}; manually close "
            f"{', '.join(f'#{n}' for n in absorbed_nums)} as absorbed into #{surviving['number']}"
        ),
    }


def suggest_pr_consolidations(
    open_prs: Sequence[Mapping[str, Any]],
    *,
    recent_prs: Sequence[PrSnapshot] = (),
    size_cap: int = DEFAULT_PR_SIZE_CAP,
) -> list[dict[str, Any]]:
    """Suggest surviving + absorbed open PR groups under ``size_cap`` (read-only)."""
    if size_cap <= 0:
        raise ValueError(f"size_cap must be positive, got {size_cap!r}")
    recent_by_number = {pr.number: pr for pr in recent_prs}
    normalized = [
        norm
        for item in open_prs
        if isinstance(item, Mapping) and (norm := _normalize_open_pr_for_consolidation(item, recent_by_number))
    ]
    by_issue: dict[int, list[dict[str, Any]]] = {}
    by_prefix: dict[str, list[dict[str, Any]]] = {}
    by_path: dict[str, list[dict[str, Any]]] = {}
    for pr in normalized:
        for issue_num in pr["linked_issues"]:
            by_issue.setdefault(issue_num, []).append(pr)
        if pr["branch_prefix"]:
            by_prefix.setdefault(pr["branch_prefix"], []).append(pr)
        for path in pr["changed_paths"]:
            by_path.setdefault(path, []).append(pr)

    candidate_groups = (
        [("linked_issue", f"#{k}", by_issue[k]) for k in sorted(by_issue) if len(by_issue[k]) >= 2]
        + [("branch_prefix", k, by_prefix[k]) for k in sorted(by_prefix) if len(by_prefix[k]) >= 2]
        + [("overlapping_paths", k, by_path[k]) for k in sorted(by_path) if len(by_path[k]) >= 2]
    )
    assigned_prs: set[int] = set()
    suggestions: list[dict[str, Any]] = []
    for group_by, group_key, members in candidate_groups:
        available = sorted(
            (m for m in members if m["number"] not in assigned_prs), key=lambda m: (m["draft"], m["number"])
        )
        surviving = (
            next((m for m in available if m["changed_lines"] <= size_cap), None) if len(available) >= 2 else None
        )
        if surviving is None:
            continue
        combined_lines, absorbed, skipped_over_cap = surviving["changed_lines"], [], []
        for cand in available:
            if cand["number"] == surviving["number"]:
                continue
            if combined_lines + cand["changed_lines"] <= size_cap:
                absorbed.append(cand)
                combined_lines += cand["changed_lines"]
            else:
                skipped_over_cap.append(cand["number"])
        if absorbed:
            assigned_prs.update(pr["number"] for pr in (surviving, *absorbed))
            suggestions.append(
                _build_consolidation_suggestion(
                    group_by, group_key, surviving, absorbed, skipped_over_cap, combined_lines, size_cap
                )
            )
    return suggestions
