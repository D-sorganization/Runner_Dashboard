"""Unified GitHub and Linear issue inventory.

Merge rules for a successfully matched pair ``(L, G)``:

| Field | Rule |
|---|---|
| `primary_source` | `"linear"` if `prefer_source == "linear"`, else `"github"`. |
| `sources` | `["linear", "github"]` (sorted alphabetically -- stable for tests). |
| `repository` | `G.repository` always. Linear may not know it. |
| `number` | `G.number` always. |
| `url` | Primary source's URL. Both URLs accessible via `linear.url` / `github.url`. |
| `title` | Primary source's title. Linear is mobile-edited; bias to it. |
| `author` | Primary source's author. |
| `assignees` | Primary source's assignees (no merging -- operators choose one tracker as authoritative). |
| `labels` | Union, deduplicated, stable order: `G.labels` first, then Linear-derived labels not already present. |
| `state` | If either side is `"closed"` -> `"closed"`. Else `"open"`. |
| `age_hours` | Minimum of the two (whichever is older -- same work, oldest creation). |
| `taxonomy` | Re-run `parse_taxonomy()` on the merged label list. Do not merge dicts. |
| `agent_claim` | `G.agent_claim` always. Lease lives in GitHub. |
| `claim_expires_at` | `G.claim_expires_at` always. |
| `linked_pr` | `G.linked_pr`. |
| `pickable` | `G.pickable AND L.pickable`. |
| `pickable_blocked_by` | Union of both lists, deduplicated. |
| `linear` | `L.linear` (the Linear-specific subdict, preserved). |
| `github` | New subdict: `{"repository": G.repository, "number": G.number, "url": G.url}`. |

Edge cases:
- Linear issue closed but GitHub issue open -> merged is closed. This is the
  conservative choice; operators may later prefer "open if either is open" but
  the safer rule ships first.
- GitHub issue with `claim:claude` lease, Linear issue Open + Started -> merged
  is unpickable with reason `active claim: claude`. The mobile user adding a
  dispatch label on Linear in this state is ignored at webhook time.
- Linear issue without `linear.github_attachments`, GitHub body without Linear
  URL -> no collapse, both surface separately. Operators see two rows; this is
  acceptable and signals a sync problem on Linear's side.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import issue_inventory
import linear_inventory
from issue_inventory import parse_taxonomy
from linear_client import LinearClient

log = logging.getLogger("dashboard")

GITHUB_ISSUE_URL_RE = re.compile(r"https?://github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)", re.IGNORECASE)
LINEAR_ISSUE_URL_RE = re.compile(r"https?://linear\.app/[^\s)>\]]+/issue/([A-Z]+-\d+)", re.IGNORECASE)


def collapse(
    github_items: list[dict],
    linear_items: list[dict],
    *,
    prefer_source: str = "linear",
) -> tuple[list[dict], int]:
    """Collapse matching Linear and GitHub issues into one unified list."""
    github_copies = [copy.deepcopy(item) for item in github_items]
    linear_copies = [copy.deepcopy(item) for item in linear_items]
    used_github_indexes: set[int] = set()
    items: list[dict] = []
    collapsed = 0

    for linear_item in linear_copies:
        match_index = _match_github_index(linear_item, github_copies, used_github_indexes)
        if match_index is None:
            items.append(_linear_only(linear_item))
            continue

        used_github_indexes.add(match_index)
        collapsed += 1
        items.append(_merge_pair(linear_item, github_copies[match_index], prefer_source=prefer_source))

    for index, github_item in enumerate(github_copies):
        if index not in used_github_indexes:
            items.append(_github_only(github_item))

    items.sort(key=lambda item: item.get("age_hours", 0))
    return items, collapsed


async def fetch_unified_issues(
    *,
    github_repos: list[str],
    linear_config: dict,
    linear_client: LinearClient | None,
    state: str = "open",
    labels: list[str] | None = None,
    assignee: str | None = None,
    pickable_only: bool = False,
    complexity: list[str] | None = None,
    effort: list[str] | None = None,
    judgement: list[str] | None = None,
    limit: int = 500,
) -> dict:
    """Fetch GitHub and Linear inventory, collapse duplicates, and return stats."""
    github_result = await issue_inventory.fetch_all_issues(
        github_repos,
        state=state,
        labels=labels,
        assignee=assignee,
        pickable_only=pickable_only,
        complexity=complexity,
        effort=effort,
        judgement=judgement,
        limit=limit,
    )
    github_items = github_result.get("items", [])
    errors = list(github_result.get("errors", []))

    if linear_client is None:
        items = [_github_only(item) for item in github_items]
        result = {
            "items": items[:limit],
            "errors": errors,
            "stats": _stats(len(github_items), 0, 0, len(github_items), 0),
        }
        log.info(
            "unified_issue_inventory: github=%s linear=0 collapsed=0 unified=%s",
            len(github_items),
            len(result["items"]),
        )
        return result

    linear_result = await linear_inventory.fetch_all_issues(
        linear_config,
        linear_client,
        state=state,
        pickable_only=pickable_only,
        complexity=complexity,
        effort=effort,
        judgement=judgement,
        limit=limit,
    )
    linear_items = linear_result.get("items", [])
    errors.extend(linear_result.get("errors", []))

    unified_items, collapsed = collapse(github_items, linear_items)
    filtered_items = _apply_unified_filters(
        unified_items, labels=labels, assignee=assignee, pickable_only=pickable_only
    )
    filtered_items = filtered_items[:limit]
    github_only = len(github_items) - collapsed
    linear_only = len(linear_items) - collapsed
    result = {
        "items": filtered_items,
        "errors": errors,
        "stats": _stats(len(github_items), len(linear_items), collapsed, github_only, linear_only),
    }
    log.info(
        "unified_issue_inventory: github=%s linear=%s collapsed=%s unified=%s",
        len(github_items),
        len(linear_items),
        collapsed,
        len(filtered_items),
    )
    return result


def _match_github_index(linear_item: dict, github_items: list[dict], used_indexes: set[int]) -> int | None:
    attachment_urls = _linear_github_attachment_urls(linear_item)
    if attachment_urls:
        for index, github_item in enumerate(github_items):
            if index in used_indexes:
                continue
            github_url = _normalise_github_issue_url(github_item.get("url"))
            if github_url and github_url in attachment_urls:
                log.debug(
                    "unified_issue_inventory: matched %s to %s by attachment",
                    _linear_identifier(linear_item),
                    github_url,
                )
                return index

    identifier = _linear_identifier(linear_item)
    if not identifier:
        return None
    for index, github_item in enumerate(github_items):
        if index in used_indexes:
            continue
        body = str(github_item.get("body") or "")
        if identifier.casefold() in {match.casefold() for match in LINEAR_ISSUE_URL_RE.findall(body)}:
            log.debug(
                "unified_issue_inventory: matched %s to %s by body url",
                identifier,
                github_item.get("url"),
            )
            return index
    return None


def _merge_pair(linear_item: dict, github_item: dict, *, prefer_source: str) -> dict:
    primary = linear_item if prefer_source == "linear" else github_item
    labels = _dedupe([*github_item.get("labels", []), *linear_item.get("labels", [])])
    blocked_by = _dedupe([*github_item.get("pickable_blocked_by", []), *linear_item.get("pickable_blocked_by", [])])

    return {
        "repository": github_item.get("repository", ""),
        "number": github_item.get("number"),
        "title": primary.get("title", ""),
        "url": primary.get("url", ""),
        "author": primary.get("author", ""),
        "assignees": list(primary.get("assignees", [])),
        "labels": labels,
        "state": "closed" if "closed" in {linear_item.get("state"), github_item.get("state")} else "open",
        "age_hours": min(_age_value(linear_item), _age_value(github_item)),
        "taxonomy": parse_taxonomy(labels),
        "agent_claim": github_item.get("agent_claim"),
        "claim_expires_at": github_item.get("claim_expires_at"),
        "linked_pr": github_item.get("linked_pr"),
        "pickable": bool(github_item.get("pickable")) and bool(linear_item.get("pickable")),
        "pickable_blocked_by": blocked_by,
        "linear": copy.deepcopy(linear_item.get("linear")),
        "github": _github_subdict(github_item),
        "sources": sorted(["linear", "github"]),
        "primary_source": "linear" if prefer_source == "linear" else "github",
    }


def _github_only(item: dict) -> dict:
    result = copy.deepcopy(item)
    result["sources"] = ["github"]
    result["primary_source"] = "github"
    result["linear"] = None
    result["github"] = _github_subdict(result)
    return result


def _linear_only(item: dict) -> dict:
    result = copy.deepcopy(item)
    result["sources"] = ["linear"]
    result["primary_source"] = "linear"
    result["github"] = None
    return result


def _github_subdict(item: dict) -> dict:
    return {"repository": item.get("repository", ""), "number": item.get("number"), "url": item.get("url", "")}


def _linear_identifier(item: dict) -> str:
    linear = item.get("linear")
    if not isinstance(linear, dict):
        return ""
    identifier = linear.get("identifier")
    return identifier if isinstance(identifier, str) else ""


def _linear_github_attachment_urls(item: dict) -> set[str]:
    linear = item.get("linear")
    if not isinstance(linear, dict):
        return set()
    urls = linear.get("github_attachments")
    if not isinstance(urls, list):
        return set()
    return {
        normalised
        for url in urls
        if isinstance(url, str) and (normalised := _normalise_github_issue_url(url)) is not None
    }


def _normalise_github_issue_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = GITHUB_ISSUE_URL_RE.search(value)
    if not match:
        return None
    parsed = urlsplit(match.group(0))
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path.lower(), "", ""))


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _age_value(item: dict) -> float:
    value = item.get("age_hours", 0)
    return value if isinstance(value, int | float) else 0.0


def _stats(github_total: int, linear_total: int, collapsed: int, github_only: int, linear_only: int) -> dict[str, int]:
    return {
        "github_total": github_total,
        "linear_total": linear_total,
        "collapsed": collapsed,
        "linear_only": linear_only,
        "github_only": github_only,
        "unified_total": github_only + linear_only + collapsed,
    }


def _apply_unified_filters(
    items: list[dict],
    *,
    labels: list[str] | None,
    assignee: str | None,
    pickable_only: bool,
) -> list[dict]:
    label_set = set(labels) if labels else set()
    result = []
    for item in items:
        if label_set and not label_set.intersection(item.get("labels", [])):
            continue
        if assignee and assignee not in item.get("assignees", []):
            continue
        if pickable_only and not item.get("pickable"):
            continue
        result.append(item)
    return result
