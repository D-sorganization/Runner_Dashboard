"""Parse Barb-style markdown state tables, flag stale rows, live-check GitHub (#3640)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from staff.workspace import ORG

log = logging.getLogger("dashboard.staff.fleet_state_audit")

PT = ZoneInfo("America/Los_Angeles")
_TABLE_ROW = re.compile(r"^\|(.+)\|$")
_REPO_REF = re.compile(
    r"\b(?P<prefix>RD|RM|GM|UD|AD)\s+#(?P<num>\d+)\b|\b(?P<full>D-sorganization/[A-Za-z0-9_.-]+)\s+#(?P<num2>\d+)\b|#(?P<plain>\d+)\b",
    re.IGNORECASE,
)
_TTL = re.compile(r"^(\d+)\s*(h|hr|hrs|hour|hours|d|day|days)$", re.IGNORECASE)
_AUTO_NULL = re.compile(r"auto\s*merge\s*(?:request)?\s*(?:is\s*)?null", re.IGNORECASE)
_RED_MAIN = re.compile(r"\bred\s*main\b|push\s+ci\s+on\s+main|default\s+branch", re.IGNORECASE)
_QUEUE = re.compile(r"\bmerge[_\s-]?group\b|\bmerge\s+queue\b", re.IGNORECASE)

REPO_ALIASES = {"RD": "Runner_Dashboard", "RM": "Repository_Management", "GM": "Gasification_Model", "UD": "UpstreamDrift", "AD": "AffineDrift"}

DEFAULT_REPO = "Runner_Dashboard"
CACHE_SUFFIX = ".fleet-state-audit.json"


@dataclass(frozen=True)
class StateRow:
    item: str
    source: str
    check: str
    verified_at_raw: str
    ttl_seconds: int
    section: str


def now_pt() -> datetime:
    return datetime.now(PT)


def default_cache_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}{CACHE_SUFFIX}")


def load_previous_report(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable previous audit at %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def parse_ttl(raw: str) -> int:
    text = str(raw or "").strip().lower()
    if not text:
        return 0
    match = _TTL.match(text.replace(" ", ""))
    if not match:
        return 0
    qty, unit = int(match.group(1)), match.group(2).lower()
    if unit.startswith("h"):
        return qty * 3600
    return qty * 86400


def parse_verified_at(raw: str, *, now: datetime) -> datetime | None:
    text = str(raw or "").strip()
    if not text or text.upper() == "UNVERIFIED":
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.replace(" PT", ""), fmt).replace(tzinfo=PT)
        except ValueError:
            continue
    return None


def _split_cells(line: str) -> list[str]:
    parts = [p.strip() for p in line.strip().strip("|").split("|")]
    return parts


def parse_state_table(text: str) -> list[StateRow]:
    """Parse the primary state table (before PR-crush lanes)."""
    section = "general"
    rows: list[StateRow] = []
    in_table = False
    for raw_line in text.splitlines():
        if raw_line.strip().startswith("## "):
            break
        match = _TABLE_ROW.match(raw_line.strip())
        if not match:
            continue
        cells = _split_cells(raw_line)
        if not cells or cells[0].lower() == "item":
            in_table = True
            continue
        if not in_table or all(set(c) <= {"-", " "} for c in cells):
            continue
        item = cells[0]
        if item.startswith("**") and item.endswith("**"):
            if "red main" in item.strip("*").lower():
                section = "red_main"
            continue
        if len(cells) < 6:
            continue
        _, source, check, verified, ttl_raw = cells[1:6]
        rows.append(
            StateRow(
                item=item,
                source=source,
                check=check,
                verified_at_raw=verified,
                ttl_seconds=parse_ttl(ttl_raw),
                section=section,
            )
        )
    return rows


def _row_key(row: StateRow) -> str:
    return row.item.strip()


def evaluate_static_flags(rows: Sequence[StateRow], *, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or now_pt()
    flags: list[dict[str, Any]] = []
    for row in rows:
        key = _row_key(row)
        if not str(row.source or "").strip().replace("-", ""):
            flags.append({"kind": "missing_source", "item": key, "section": row.section})
        verified = parse_verified_at(row.verified_at_raw, now=moment)
        ttl = row.ttl_seconds or 3600
        stale = verified is None or (moment - verified) > timedelta(seconds=ttl)
        if stale:
            flags.append(
                {
                    "kind": "stale",
                    "item": key,
                    "section": row.section,
                    "verified_at": row.verified_at_raw,
                    "ttl_seconds": ttl,
                }
            )
    return flags


def extract_pr_refs(row: StateRow, *, default_repo: str = DEFAULT_REPO) -> list[tuple[str, int]]:
    blob = " ".join((row.source, row.check, row.item))
    refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in _REPO_REF.finditer(blob):
        if match.group("prefix"):
            repo = REPO_ALIASES.get(match.group("prefix").upper(), default_repo)
            num = int(match.group("num"))
        elif match.group("full"):
            repo = match.group("full").split("/", 1)[1]
            num = int(match.group("num2"))
        else:
            repo, num = default_repo, int(match.group("plain"))
        full = repo if "/" in repo else f"{ORG}/{repo}"
        key = (full, num)
        if key not in seen:
            seen.add(key)
            refs.append(key)
    return refs


def repo_full(name: str) -> str:
    raw = name.strip()
    if "/" in raw:
        return raw
    return f"{ORG}/{REPO_ALIASES.get(raw.upper(), raw)}"


def _expects_auto_merge_off(check: str) -> bool:
    return bool(_AUTO_NULL.search(check or ""))


def _needs_red_main(check: str, section: str) -> bool:
    return section == "red_main" or bool(_RED_MAIN.search(check or ""))


def _needs_queue(check: str) -> bool:
    return bool(_QUEUE.search(check or ""))


FetchPr = Callable[[str, int], Awaitable[dict[str, Any]]]
FetchRedMain = Callable[[str], Awaitable[dict[str, Any]]]


async def _noop_pr(*_a: Any) -> dict[str, Any]:
    return {"merged": False, "state": "open", "auto_merge": None, "head": {"sha": ""}}


async def _noop_red_main(*_a: Any) -> dict[str, Any]:
    return {"red_main": False, "conclusion": "success"}


async def live_check_row(
    row: StateRow,
    *,
    fetch_pr: FetchPr,
    fetch_red_main: FetchRedMain,
) -> dict[str, Any]:
    problems: list[str] = []
    checks: dict[str, Any] = {}
    pr_refs = extract_pr_refs(row)
    if pr_refs:
        for full, number in pr_refs:
            pr = await fetch_pr(full, number)
            armed = pr.get("auto_merge") is not None
            checks[f"pr/{number}"] = {
                "repo": full,
                "merged": bool(pr.get("merged")),
                "state": pr.get("state"),
                "auto_merge_armed": armed,
                "url": pr.get("html_url"),
            }
            if _expects_auto_merge_off(row.check) and armed:
                problems.append("auto_merge_armed")
    if _needs_red_main(row.check, row.section):
        target = repo_full(pr_refs[0][0].split("/", 1)[1]) if pr_refs else repo_full(DEFAULT_REPO)
        rm = await fetch_red_main(target)
        checks["red_main"] = rm
        if rm.get("red_main"):
            problems.append("red_main")
    ok = not problems
    return {"item": _row_key(row), "ok": ok, "problems": problems, "checks": checks}


async def live_check_all(rows: Sequence[StateRow], **overrides: Any) -> list[dict[str, Any]]:
    import gh_client  # noqa: PLC0415

    async def fetch_pr(full: str, number: int) -> dict[str, Any]:
        body = await gh_client.get(f"/repos/{full}/pulls/{number}")
        return body if isinstance(body, dict) else {}

    async def fetch_red_main(full: str) -> dict[str, Any]:
        repo = await gh_client.get(f"/repos/{full}")
        branch = (repo.get("default_branch") if isinstance(repo, dict) else None) or "main"
        runs = await gh_client.get(f"/repos/{full}/actions/runs?branch={branch}&event=push&status=completed&per_page=1")
        latest = (runs.get("workflow_runs") or [{}])[0] if isinstance(runs, dict) else {}
        conclusion = latest.get("conclusion")
        return {"red_main": conclusion == "failure", "conclusion": conclusion, "branch": branch}

    async def fetch_queue_meta(full: str) -> dict[str, Any]:
        runs = await gh_client.get(f"/repos/{full}/actions/runs?event=merge_group&per_page=30")
        workflow_runs = runs.get("workflow_runs") if isinstance(runs, dict) else []
        active = frozenset({"queued", "in_progress"})
        return {"depth": sum(1 for r in workflow_runs if r.get("status") in active)}

    fp, fr = overrides.get("fetch_pr") or fetch_pr, overrides.get("fetch_red_main") or fetch_red_main
    fq = overrides.get("fetch_queue_meta") or fetch_queue_meta
    out: list[dict[str, Any]] = []
    for row in rows:
        if not (extract_pr_refs(row) or _needs_red_main(row.check, row.section) or _needs_queue(row.check)):
            continue
        live = await live_check_row(row, fetch_pr=fp, fetch_red_main=fr)
        if _needs_queue(row.check) and extract_pr_refs(row):
            live.setdefault("checks", {})["merge_queue"] = await fq(extract_pr_refs(row)[0][0])
        out.append(live)
    return out


def build_report(
    *,
    path: Path,
    rows: Sequence[StateRow],
    static_flags: Sequence[Mapping[str, Any]],
    live_results: Sequence[Mapping[str, Any]],
    previous: Mapping[str, Any] | None,
    live_enabled: bool,
) -> dict[str, Any]:
    tally = {
        "rows": len(rows),
        "stale": sum(1 for f in static_flags if f.get("kind") == "stale"),
        "missing_source": sum(1 for f in static_flags if f.get("kind") == "missing_source"),
        "live_failures": sum(1 for r in live_results if not r.get("ok")),
        "live_checked": len(live_results),
    }
    flag_keys = {f"{f['kind']}:{f['item']}" for f in static_flags}
    prev_keys = {f"{f.get('kind')}:{f.get('item')}" for f in (previous or {}).get("flags", []) if isinstance(f, Mapping)}
    before = (previous or {}).get("tally") if isinstance((previous or {}).get("tally"), dict) else {}
    diff_tally = {
        k: {"before": int(before.get(k, 0)), "after": int(tally[k])}
        for k in tally
        if previous and (k in before or k in tally)
    }
    return {
        "path": str(path),
        "generated_at": now_pt().isoformat(),
        "live_enabled": live_enabled,
        "tally": tally,
        "flags": list(static_flags),
        "live": list(live_results),
        "diff": {
            "tally": diff_tally,
            "flags": {"added": sorted(flag_keys - prev_keys), "removed": sorted(prev_keys - flag_keys), "changed": []},
        },
    }


async def run_audit_async(path: Path, *, live: bool = True, previous_path: Path | None = None, write_cache: bool = False) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    rows = parse_state_table(text)
    static = evaluate_static_flags(rows)
    previous_file = previous_path or default_cache_path(path)
    previous = load_previous_report(previous_file)
    live_results: list[dict[str, Any]] = []
    if live:
        live_results = await live_check_all(rows)
    report = build_report(
        path=path,
        rows=rows,
        static_flags=static,
        live_results=live_results,
        previous=previous,
        live_enabled=live,
    )
    if write_cache:
        previous_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def run_audit(path: Path, *, live: bool = True, previous_path: Path | None = None, write_cache: bool = False) -> dict[str, Any]:
    import anyio  # noqa: PLC0415

    async def _main() -> dict[str, Any]:
        return await run_audit_async(path, live=live, previous_path=previous_path, write_cache=write_cache)

    return anyio.run(_main)
