"""Read-only inventory of open PRs vs claims (issue #2905 / epic #2900).

Classifies each open PR as ``claimed``, ``backfillable``, ``duplicate_candidate``,
or ``orphan`` from stdin JSON without closing, commenting on, or labelling any PR.
"""

from __future__ import annotations

import argparse
import datetime as _dt_mod
import json
import logging
import re
import shlex
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, TextIO

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

UTC = _dt_mod.timezone.utc  # noqa: UP017

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared_scripts.andon_fingerprint import fingerprint as _compute_fingerprint  # noqa: E402

log = logging.getLogger(__name__)

CLAIM_LABEL_PREFIX: Final[str] = "claim:"
VALID_CLASSIFICATIONS: Final[frozenset[str]] = frozenset({"claimed", "backfillable", "duplicate_candidate", "orphan"})

_ISSUE_REF_RE = re.compile(
    r"\b(?:closes|close|closed|fixes|fix|fixed|resolves|resolve|resolved|refs|ref)\s+#(\d+)\b",
    re.IGNORECASE,
)
_FINGERPRINT_RE = re.compile(r"andon-fingerprint\s*:\s*([0-9a-fA-F]{8,64})", re.IGNORECASE)
_MUTATING_HTTP_METHODS: Final[frozenset[str]] = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_MUTATING_GH_SUBCOMMANDS: Final[frozenset[str]] = frozenset(
    {"close", "merge", "edit", "comment", "create", "delete", "reopen", "lock", "unlock", "ready", "review"}
)


class ReadOnlyViolation(RuntimeError):
    """Raised when a GitHub CLI command attempts a mutating operation."""


@dataclass(frozen=True)
class InventoryRow:
    """Single PR classification entry in the read-only inventory report."""

    number: int
    title: str
    head_ref_name: str
    linked_issue: int | None
    claim_agent: str | None
    classification: str
    fingerprint: str | None
    stale_hours: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint_from_ci_payload(payload: Mapping[str, Any]) -> str:
    """Delegate CI payload fingerprinting to shared_scripts.andon_fingerprint (#2901)."""
    return _compute_fingerprint(payload).fingerprint


def guard_read_only_github(command: Sequence[str] | str) -> list[str]:
    """DbC guard: verify a ``gh`` command is strictly read-only; raise ReadOnlyViolation otherwise."""
    tokens = shlex.split(command) if isinstance(command, str) else [str(t) for t in command]
    if not tokens:
        raise ReadOnlyViolation("empty command is not permitted")
    for idx, token in enumerate(tokens):
        upper = token.upper()
        if upper in _MUTATING_HTTP_METHODS:
            raise ReadOnlyViolation(f"mutating HTTP method {upper} is forbidden")
        if token in ("-X", "--method") and idx + 1 < len(tokens):
            method = tokens[idx + 1].upper()
            if method != "GET":
                raise ReadOnlyViolation(f"non-GET method {method} is forbidden")
        for prefix in ("-X", "--method="):
            if token.startswith(prefix):
                method = token[len(prefix) :].upper()
                if method and method != "GET":
                    raise ReadOnlyViolation(f"non-GET method {method} is forbidden")
        if token.lower() in _MUTATING_GH_SUBCOMMANDS and idx <= 3:
            raise ReadOnlyViolation(f"mutating gh subcommand {token!r} is forbidden")
        if "mutation" in token.lower():
            raise ReadOnlyViolation("GraphQL mutation is forbidden in read-only inventory")
    return tokens


def extract_linked_issue(pr: Mapping[str, Any]) -> int | None:
    """Extract linked issue number from explicit field, closingIssuesReferences, or PR body/title."""
    explicit = pr.get("linked_issue")
    if isinstance(explicit, int) and explicit > 0:
        return explicit
    refs = pr.get("closingIssuesReferences")
    if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
        for item in refs:
            if isinstance(item, Mapping) and isinstance(item.get("number"), int):
                return int(item["number"])
    for field in ("body", "title"):
        text = pr.get(field)
        if isinstance(text, str):
            match = _ISSUE_REF_RE.search(text)
            if match:
                return int(match.group(1))
    return None


def extract_claim_agent(pr: Mapping[str, Any]) -> str | None:
    """Extract agent name from any ``claim:<agent>`` label on the PR."""
    raw_labels = pr.get("labels")
    if not isinstance(raw_labels, Sequence) or isinstance(raw_labels, (str, bytes)):
        return None
    for item in raw_labels:
        name = item.get("name") if isinstance(item, Mapping) else item if isinstance(item, str) else None
        if isinstance(name, str) and name.startswith(CLAIM_LABEL_PREFIX):
            agent = name[len(CLAIM_LABEL_PREFIX) :].strip()
            if agent:
                return agent
    return None


def extract_fingerprint(pr: Mapping[str, Any]) -> str | None:
    """Extract or compute an andon fingerprint from a PR mapping."""
    direct = pr.get("fingerprint")
    if isinstance(direct, str) and direct.strip():
        return direct.strip().lower()
    ci_payload = pr.get("ci_payload") or pr.get("andon_payload")
    if isinstance(ci_payload, Mapping):
        return fingerprint_from_ci_payload(ci_payload)
    body = pr.get("body")
    if isinstance(body, str):
        match = _FINGERPRINT_RE.search(body)
        if match:
            return match.group(1).lower()
    return None


def _compute_stale_hours(pr: Mapping[str, Any], now: datetime) -> float | None:
    raw_ts = pr.get("updatedAt") or pr.get("updated_at") or pr.get("createdAt")
    if not isinstance(raw_ts, str) or not raw_ts.strip():
        return None
    normalized = raw_ts.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta_hours = (now - dt).total_seconds() / 3600.0
    return round(max(0.0, delta_hours), 2)


def classify_pr(
    pr: Mapping[str, Any],
    *,
    seen_fingerprints: set[str] | frozenset[str] | None = None,
    now: datetime | None = None,
) -> InventoryRow:
    """Classify a single open PR mapping into an InventoryRow."""
    if not isinstance(pr, Mapping):
        raise ValueError("PR entry must be a JSON object")
    number = pr.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ValueError(f"PR number must be a positive integer, got {number!r}")

    ref_now = now or datetime.now(UTC)
    linked_issue = extract_linked_issue(pr)
    claim_agent = extract_claim_agent(pr)
    fp = extract_fingerprint(pr)
    is_dup = bool(fp and seen_fingerprints and fp in seen_fingerprints)

    if is_dup:
        classification = "duplicate_candidate"
    elif linked_issue is not None and claim_agent is not None:
        classification = "claimed"
    elif linked_issue is not None:
        classification = "backfillable"
    else:
        classification = "orphan"

    assert classification in VALID_CLASSIFICATIONS
    return InventoryRow(
        number=number,
        title=str(pr.get("title") or ""),
        head_ref_name=str(pr.get("headRefName") or pr.get("head_ref_name") or ""),
        linked_issue=linked_issue,
        claim_agent=claim_agent,
        classification=classification,
        fingerprint=fp,
        stale_hours=_compute_stale_hours(pr, ref_now),
    )


def build_inventory(
    prs: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> list[InventoryRow]:
    """Build the full inventory for ``prs``, ensuring 100% row coverage."""
    if not isinstance(prs, Sequence) or isinstance(prs, (str, bytes)):
        raise ValueError("prs must be a sequence of PR objects")
    ref_now = now or datetime.now(UTC)

    fp_counts: Counter[str] = Counter()
    for pr in prs:
        if isinstance(pr, Mapping):
            fp = extract_fingerprint(pr)
            if fp:
                fp_counts[fp] += 1

    seen_fps: set[str] = set()
    rows: list[InventoryRow] = []
    for pr in prs:
        fp = extract_fingerprint(pr) if isinstance(pr, Mapping) else None
        claim = extract_claim_agent(pr) if isinstance(pr, Mapping) else None
        issue = extract_linked_issue(pr) if isinstance(pr, Mapping) else None
        # A repeated fingerprint is a duplicate candidate unless this row is the first claimed PR for it.
        dup_set: set[str] = set(seen_fps)
        if fp and fp_counts[fp] > 1 and not (issue is not None and claim is not None and fp not in seen_fps):
            dup_set.add(fp)
        row = classify_pr(pr, seen_fingerprints=dup_set, now=ref_now)
        if fp:
            seen_fps.add(fp)
        rows.append(row)

    assert len(rows) == len(prs), "postcondition failed: inventory row count must match input PR count"
    return rows


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """CLI entrypoint reading PR list JSON from ``--stdin-json`` and emitting a JSON report."""
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr

    parser = argparse.ArgumentParser(description="Read-only open PR vs claim inventory (#2905).")
    parser.add_argument(
        "--stdin-json",
        action="store_true",
        help="Read open PR JSON array from stdin (required; no live writes).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.stdin_json:
        err_stream.write("::error::--stdin-json is required (read-only mode)\n")
        return 2

    try:
        payload = json.load(in_stream)
    except json.JSONDecodeError as exc:
        err_stream.write(f"::error::invalid stdin JSON: {exc}\n")
        return 2

    if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
        err_stream.write("::error::stdin JSON must be an array of PR objects\n")
        return 2

    try:
        rows = build_inventory(payload)
    except ValueError as exc:
        err_stream.write(f"::error::{exc}\n")
        return 2

    counts: Counter[str] = Counter(r.classification for r in rows)
    report = {
        "total": len(rows),
        "counts": {k: counts.get(k, 0) for k in ("claimed", "backfillable", "duplicate_candidate", "orphan")},
        "items": [r.to_dict() for r in rows],
    }
    out_stream.write(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
