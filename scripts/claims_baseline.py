"""Write up the claims-path JSONL baseline (issue #4595).

Reads the records appended by ``coordination.claim_telemetry`` (when
``CLAIMS_BASELINE_FILE`` is set on the Desk hub) and renders a markdown report
with p50/p95 of ``GET /api/coordination/claims`` latency, the hub-visible
GitHub-call proxy per request, blocked lane exits and expired holds. These are
the numbers later read-model slices set targets from, instead of guessing.

Usage::

    python scripts/claims_baseline.py --file claims.jsonl [--quiet-baseline 5.0] [--out report.md]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _percentile(sorted_values: list[float], percentile: float) -> float | None:
    """Nearest-rank percentile, or ``None`` when there are no samples."""
    if not sorted_values:
        return None
    index = max(0, int(math.ceil(percentile / 100.0 * len(sorted_values))) - 1)
    return sorted_values[index]


def load_records(path: Path) -> list[dict[str, Any]]:
    """Read one JSON object per line, skipping blank lines and unparseable lines."""
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def build_report(records: list[dict[str, Any]], *, quiet_baseline_s: float = 5.0) -> dict[str, Any]:
    """Aggregate records into the report numbers (pure; unit-testable)."""
    durations = sorted(float(r["duration_s"]) for r in records if r.get("kind") == "claim_check")
    by_result = Counter(str(r.get("result")) for r in records if r.get("kind") == "claim_check")
    rm_calls = Counter(str(r["script"]) for r in records if r.get("kind") == "rm_subprocess")
    rm_timeouts = Counter(str(r["script"]) for r in records if r.get("kind") == "rm_subprocess" and r.get("timed_out"))
    blocked = Counter(str(r.get("reason")) for r in records if r.get("kind") == "blocked_exit")
    expired_ages = [float(r["age_s"]) for r in records if r.get("kind") == "expired_hold"]

    total = len(durations)
    subprocess_calls = sum(rm_calls.values())
    return {
        "claims_requests_total": total,
        "p50_s": _percentile(durations, 50.0),
        "p95_s": _percentile(durations, 95.0),
        "quiet_baseline_s": quiet_baseline_s,
        "by_result": dict(by_result),
        "rm_subprocess_calls": dict(rm_calls),
        "rm_subprocess_timeouts": dict(rm_timeouts),
        "github_calls_per_request": round(subprocess_calls / total, 3) if total else None,
        "blocked_exits": dict(blocked),
        "expired_holds_count": len(expired_ages),
        "expired_holds_max_age_s": round(max(expired_ages), 3) if expired_ages else None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render the report as markdown, without pre-declaring any latency target."""
    lines = [
        "# Claims path baseline (issue #4595)",
        "",
        f"- Claims requests recorded: **{report['claims_requests_total']}**",
        f"- p50 of `GET /api/coordination/claims` latency: "
        f"{_fmt(report['p50_s'])} vs {report['quiet_baseline_s']}s quiet single-call figure",
        f"- p95 of `GET /api/coordination/claims` latency: "
        f"{_fmt(report['p95_s'])} vs {report['quiet_baseline_s']}s quiet single-call figure",
        "",
        "## Outcomes by result",
        "",
        _kv_table(report["by_result"]),
        "",
        "## GitHub-call proxy (RM subprocesses per claims request)",
        "",
        f"- Total RM subprocess calls: {sum(report['rm_subprocess_calls'].values())}",
        f"- RM subprocess calls per claims request: {report['github_calls_per_request']}",
        _kv_table(report["rm_subprocess_calls"]),
        "",
        "Each `check_agent_claim` subprocess is RM's `gh issue view` (full comments) plus "
        "`gh pr list --limit 200`; an `agent_communicate` entry is a board read. RM-side GitHub "
        "calls inside those subprocesses are not visible to the hub yet and are counted at the "
        "subprocess boundary.",
        "",
        "## Blocked lane exits",
        "",
        _kv_table(report["blocked_exits"]),
        "",
        f"- Subprocess timeouts by script: {report['rm_subprocess_timeouts'] or '{}'}",
        f"- Expired holds observed: **{report['expired_holds_count']}** "
        f"(max age {_fmt(report['expired_holds_max_age_s'], suffix='s')})",
        "",
        "## Notes",
        "",
        "No latency or GitHub-call target is declared here; later slices set targets from this "
        "baseline, not from a guess. Merges-per-hour-per-repo is an outcome signal recorded by "
        "the merge-queue batching path (`coordination/queue_batching.py`), not the claims path.",
        "",
    ]
    return "\n".join(lines)


def _fmt(value: float | None, *, suffix: str = "s") -> str:
    return f"{value:.3f}{suffix}" if value is not None else "n/a"


def _kv_table(mapping: dict[str, Any]) -> str:
    if not mapping:
        return "(none)"
    return "\n".join(f"- `{key}`: {value}" for key, value in sorted(mapping.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True, help="claims JSONL baseline file")
    parser.add_argument("--quiet-baseline", type=float, default=5.0, help="quiet single-call seconds (default 5.0)")
    parser.add_argument("--out", type=Path, default=None, help="write markdown here instead of stdout")
    args = parser.parse_args(argv)

    report = build_report(load_records(args.file), quiet_baseline_s=args.quiet_baseline)
    markdown = render_markdown(report)
    if args.out:
        args.out.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
