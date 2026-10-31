"""CLI: Bob's wave-scoring (RD#3642) — score a forecast table's lane/issue
items against actual merges in a window. Read-only, one batched GraphQL call."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from staff.bob_forecast_score import (  # noqa: E402
    REPO_ALIASES,
    build_merged_prs_query,
    parse_forecast_table,
    parse_merged_prs_response,
    score_forecast,
    summarize,
)

_RUNNER = Callable[..., subprocess.CompletedProcess[str]]
_DEFAULT_REPO = "D-sorganization/Runner_Dashboard"


def _repo_full(name: str) -> str:
    if "/" in name:
        return name
    return REPO_ALIASES.get(name.upper(), f"D-sorganization/{name}")


def fetch_merged_prs_payload(
    repos: Sequence[str], *, since: datetime, until: datetime, run: _RUNNER = subprocess.run
) -> dict[str, object]:
    query = build_merged_prs_query(repos, since=since, until=until)
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    response = run(cmd, capture_output=True, text=True, check=False, timeout=60)
    if response.returncode != 0:
        raise RuntimeError(f"gh api graphql failed: {response.stderr.strip()}")
    payload = json.loads(response.stdout)
    if not isinstance(payload, dict):
        raise ValueError("gh api graphql returned a non-object response")
    return payload


def run_bob_forecast_score(
    markdown_text: str,
    *,
    default_repo: str,
    since: datetime,
    until: datetime,
    now: datetime,
    run: _RUNNER = subprocess.run,
) -> dict[str, object]:
    items = parse_forecast_table(markdown_text, default_repo=default_repo)
    repos = sorted({item.repo for item in items}) or [default_repo]
    payload = fetch_merged_prs_payload(repos, since=since, until=until, run=run)
    merged_prs = parse_merged_prs_response(payload, repos)
    verdicts = score_forecast(items, merged_prs, window_start=since, window_end=until, now=now)
    report = summarize(verdicts)
    report["generated_at"] = now.isoformat()
    report["window"] = {"since": since.isoformat(), "until": until.isoformat()}
    report["verdicts"] = [
        {
            "lane": v.item.lane,
            "repo": v.item.repo,
            "issue_number": v.item.issue_number,
            "verdict": v.verdict,
            "merged_pr_number": v.merged_pr_number,
        }
        for v in verdicts
    ]
    return report


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main(argv: Sequence[str] | None = None, *, run: _RUNNER = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--path", required=True, type=Path, help="Markdown file with the forecast table.")
    parser.add_argument("--default-repo", default=_DEFAULT_REPO, help="owner/name for bare #N references.")
    parser.add_argument("--since", type=_parse_iso, help="Window start (ISO 8601); default now - 6h.")
    parser.add_argument("--until", type=_parse_iso, help="Window end (ISO 8601); default now.")
    args = parser.parse_args(argv)
    if not args.path.is_file():
        print(f"missing forecast file: {args.path}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    until = args.until or now
    since = args.since or (until - timedelta(hours=6))
    report = run_bob_forecast_score(
        args.path.read_text(encoding="utf-8"),
        default_repo=_repo_full(args.default_repo),
        since=since,
        until=until,
        now=now,
        run=run,
    )
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
