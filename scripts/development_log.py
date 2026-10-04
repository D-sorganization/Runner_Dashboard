#!/usr/bin/env python3
# Vendored from Repository_Management Project_Template/shared_scripts/development_log.py
# (commit dac5db339cd9a62d3a8d49f7d1aebc3e20fbd9ba; pending RM#1939, RM-5 / Repository_Management#1894).
# Re-sync from upstream; do not fork. No local changes except ruff format at
# this repository's line length. The sibling imports fall back to file paths,
# so the six change-fragment modules work side by side in ``scripts/``.
# Vendored files follow upstream size; any line-length split happens in RM (RM#1938).
"""Canonical development-log schema validation for the repository fleet.

Where `handoff_validator` answers "how do I resume the session in front of
me", the development log answers "what is being built in this repository, and
where does each thing stand". It is a fixed-size state table, not a journal:
one entry per feature, updated **in place** from proposal to ship.

That distinction is the whole design. Append-only agent logs grow without
bound, bury the current state, and are eventually ignored — which is worse
than having none, because they still look authoritative. A closed field
schema with a validator stays useful indefinitely.

This module is portable: it is copied fleet-wide alongside
`handoff_validator.py` and must not import anything outside the standard
library.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Import the sibling validator by package when available, else by file path.
# The fleet copies these two modules side by side into repositories that do not
# expose `shared_scripts` as an importable package, so a bare package import is
# not portable.
try:  # pragma: no cover - exercised by whichever branch the host supports
    from shared_scripts.handoff_validator import (
        SECRET_PATTERNS,
        is_implementation_file,
    )
except ImportError:  # pragma: no cover
    import importlib.util

    _sibling = Path(__file__).with_name("handoff_validator.py")
    _spec = importlib.util.spec_from_file_location("_fleet_handoff", _sibling)
    if _spec is None or _spec.loader is None:  # pragma: no cover
        raise
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["_fleet_handoff"] = _mod
    _spec.loader.exec_module(_mod)
    SECRET_PATTERNS = _mod.SECRET_PATTERNS
    is_implementation_file = _mod.is_implementation_file

CANONICAL_RELATIVE_PATH = Path("docs") / "development" / "DEVELOPMENT_LOG.md"

OVERRIDE_MARKER = re.compile(
    r"<!--\s*CANONICAL-DEVELOPMENT-LOG:\s*([^\s>]+)\s*-->",
)

# The closed state set. An open set invites narration, which is what turns a
# state table back into a journal.
ACTIVE_STATES = frozenset({"proposed", "in_progress", "in_review"})
TERMINAL_STATES = frozenset({"shipped", "abandoned"})
PAUSED_STATES = frozenset({"parked"})
VALID_STATES = ACTIVE_STATES | TERMINAL_STATES | PAUSED_STATES

PORTFOLIO_HEADER = re.compile(
    r"^-\s+\*\*Portfolio:\*\*\s*[`\"']?(?P<portfolio>[a-zA-Z0-9_\-]+)[`\"']?",
    re.MULTILINE | re.IGNORECASE,
)
WIP_LIMIT_HEADER = re.compile(
    r"^-\s+\*\*WIP limit:\*\*\s*[`\"']?(?P<limit>\d+)[`\"']?",
    re.MULTILINE | re.IGNORECASE,
)

# Entry ids. `DL-#<issue>` is the form for every new entry
# (Repository_Management#1520): the governing issue number is unique by
# construction, so two concurrent pull requests can never pick the same id and
# never conflict over the next serial. `DL-0001`-style serial ids are the
# pre-#1520 form and stay valid so existing entries need no rewrite — they are
# already unique — but new ones must not use it.
# The id and title are separated by a middle dot or an en/em dash; fleet logs
# use all of them (Runner_Dashboard's canonical log uses an em dash). A plain
# hyphen is deliberately not accepted: RM's own log has hyphen-headed entries
# that were never validated and would surface unrelated findings.
ENTRY_HEADING = re.compile(r"^###\s+(DL-(?:#\d+|\d{4}))\s+[·–—]\s+(.+?)\s*$", re.MULTILINE)
LEGACY_ENTRY_ID = re.compile(r"^DL-\d{4}$")
ISSUE_KEYED_ENTRY_ID = re.compile(r"^DL-#(\d+)$")
FIELD_LINE = re.compile(r"^-\s+\*\*(?P<key>[A-Za-z ]+?):\*\*\s*(?P<value>.*?)\s*$")
PLACEHOLDER = re.compile(r"<[^>\n]+>")
SHA_IN_TEXT = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Fields every entry carries, regardless of state.
BASE_FIELDS = ("State", "Owner", "PR", "Paths", "Started", "Last verified", "Summary")
# Additional fields required while an entry is still live.
ACTIVE_ONLY_FIELDS = ("Issue", "Next step")
# Branch only makes sense once code exists.
BRANCH_STATES = frozenset({"in_progress", "in_review"})

# A live entry must name a real governing issue. These placeholders satisfy a
# non-empty check while leaving the entry orphaned by the fleet's own
# definition, so they are rejected for entries in an active state.
SENTINEL_ISSUE_VALUES = frozenset(
    {
        "-",
        "n/a",
        "none",
        "not applicable",
        "not created",
        "tbd",
        "todo",
        "unknown",
    }
)

WARN_ACTIVE_ENTRIES = 30
# Owner limits (Repository_Management#1785): the archiver keeps logs small, so
# the size cap is a backstop rather than a routine blocker.
MAX_ACTIVE_ENTRIES = 75
MAX_BYTES = 200_000
ARCHIVE_AFTER_DAYS = 14
LEADING_DATE = re.compile(r"^\s*`?(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class DevLogFinding:
    """A single governance finding against a development log."""

    path: Path
    line: int | None
    kind: str
    message: str
    remediation: str


@dataclass
class Entry:
    """One parsed development-log entry."""

    entry_id: str
    title: str
    line: int
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def state(self) -> str:
        """Normalised state string, empty when absent."""
        raw = self.fields.get("State", "").strip()
        if not raw:
            return ""
        return raw.split()[0].strip("`").lower()

    @property
    def is_active(self) -> bool:
        """True when the entry still represents live work."""
        return self.state in ACTIVE_STATES


def resolve_canonical_devlog_path(repo_root: Path) -> Path:
    """Resolve the canonical development-log path for a repository."""
    agents_path = repo_root / "AGENTS.md"
    if agents_path.is_file():
        try:
            text = agents_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        marker = OVERRIDE_MARKER.search(text)
        if marker:
            return repo_root / marker.group(1).strip()
    return repo_root / CANONICAL_RELATIVE_PATH


def requires_devlog_update(changed_paths: Iterable[str]) -> bool:
    """True when any changed path is an implementation file."""
    return any(is_implementation_file(path) for path in changed_paths)


def parse_entries(content: str) -> list[Entry]:
    """Parse development-log entries in document order."""
    lines = content.splitlines()
    entries: list[Entry] = []
    current: Entry | None = None
    for index, raw in enumerate(lines, start=1):
        heading = ENTRY_HEADING.match(raw)
        if heading:
            current = Entry(
                entry_id=heading.group(1),
                title=heading.group(2).strip(),
                line=index,
            )
            entries.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("#"):
            current = None
            continue
        match = FIELD_LINE.match(raw)
        if match:
            current.fields[match.group("key").strip()] = match.group("value").strip()
    return entries


def _validate_entry(entry: Entry, path: Path) -> list[DevLogFinding]:
    """Validate one entry against the closed schema."""
    findings: list[DevLogFinding] = []

    state = entry.state
    if not state:
        findings.append(
            DevLogFinding(
                path=path,
                line=entry.line,
                kind="missing_state",
                message=f"{entry.entry_id} has no State field.",
                remediation=f"Add '- **State:** <one of {sorted(VALID_STATES)}>'.",
            )
        )
    elif state not in VALID_STATES:
        findings.append(
            DevLogFinding(
                path=path,
                line=entry.line,
                kind="invalid_state",
                message=f"{entry.entry_id} has unknown state '{state}'.",
                remediation=f"Use one of: {', '.join(sorted(VALID_STATES))}.",
            )
        )

    # An issue-keyed id must actually name the entry's governing issue,
    # otherwise the id is unique but meaningless and the entry/issue join the
    # orphan detection depends on silently breaks (Repository_Management#1520).
    issue_keyed = ISSUE_KEYED_ENTRY_ID.match(entry.entry_id)
    if issue_keyed:
        issue_field = entry.fields.get("Issue", "")
        if f"#{issue_keyed.group(1)}" not in issue_field:
            findings.append(
                DevLogFinding(
                    path=path,
                    line=entry.line,
                    kind="entry_id_issue_mismatch",
                    message=(
                        f"{entry.entry_id} is keyed by issue "
                        f"#{issue_keyed.group(1)}, which its Issue field "
                        f"({issue_field or 'empty'}) does not name."
                    ),
                    remediation=("Key the entry by its governing issue, or correct the Issue field."),
                )
            )

    required = list(BASE_FIELDS)
    if state in ACTIVE_STATES:
        required.extend(ACTIVE_ONLY_FIELDS)
    if state in BRANCH_STATES:
        required.append("Branch")
    if state == "parked":
        required.append("Parked")

    for key in required:
        value = entry.fields.get(key, "").strip()
        if not value:
            findings.append(
                DevLogFinding(
                    path=path,
                    line=entry.line,
                    kind="missing_field",
                    message=f"{entry.entry_id} is missing required field '{key}'.",
                    remediation=f"Add '- **{key}:** ...' to {entry.entry_id}.",
                )
            )
        elif PLACEHOLDER.search(value):
            findings.append(
                DevLogFinding(
                    path=path,
                    line=entry.line,
                    kind="placeholder",
                    message=(f"{entry.entry_id} field '{key}' holds a placeholder."),
                    remediation="Replace angle-bracket placeholders with real values.",
                )
            )

    issue = entry.fields.get("Issue", "").strip()
    if state in ACTIVE_STATES and issue:
        bare = issue.strip("`").split("—")[0].strip().rstrip(".").lower()
        if bare in SENTINEL_ISSUE_VALUES:
            findings.append(
                DevLogFinding(
                    path=path,
                    line=entry.line,
                    kind="sentinel_issue",
                    message=(
                        f"{entry.entry_id} is {state} but its Issue is "
                        f"'{issue}'. A live entry must name a real governing "
                        "issue."
                    ),
                    remediation="Open a governing issue and reference it.",
                )
            )

    verified = entry.fields.get("Last verified", "")
    if verified and not ISO_DATE.search(verified):
        findings.append(
            DevLogFinding(
                path=path,
                line=entry.line,
                kind="unusable_last_verified",
                message=f"{entry.entry_id} 'Last verified' has no YYYY-MM-DD date.",
                remediation="Write 'Last verified' as '<YYYY-MM-DD> (`<sha>`)'.",
            )
        )
    if verified and not SHA_IN_TEXT.search(verified):
        findings.append(
            DevLogFinding(
                path=path,
                line=entry.line,
                kind="unusable_last_verified",
                message=f"{entry.entry_id} 'Last verified' has no commit SHA.",
                remediation="Include the verifying commit SHA in 'Last verified'.",
            )
        )
    return findings


def validate_devlog_content(
    content: str,
    path: Path,
    is_template: bool = False,
    check_portfolio_wip: bool = False,
) -> list[DevLogFinding]:
    """Validate development-log content against the canonical schema."""
    findings: list[DevLogFinding] = []

    if "## Active" not in content:
        findings.append(
            DevLogFinding(
                path=path,
                line=None,
                kind="missing_heading",
                message="Development log has no '## Active' section.",
                remediation="Create the log from docs/templates/DEVELOPMENT_LOG.md.",
            )
        )

    for pattern, secret_type in SECRET_PATTERNS:
        if pattern.search(content):
            findings.append(
                DevLogFinding(
                    path=path,
                    line=None,
                    kind="secret_detected",
                    message=f"Potential {secret_type} detected in development log.",
                    remediation="Remove credentials or tokens from the log.",
                )
            )

    entries = parse_entries(content)
    seen: dict[str, int] = {}
    for entry in entries:
        if entry.entry_id in seen:
            findings.append(
                DevLogFinding(
                    path=path,
                    line=entry.line,
                    kind="duplicate_entry",
                    message=(
                        f"{entry.entry_id} appears twice "
                        f"(first at line {seen[entry.entry_id]}). "
                        "Entries are updated in place, never duplicated."
                    ),
                    remediation="Merge the duplicate into the original entry.",
                )
            )
            continue
        seen[entry.entry_id] = entry.line

    if is_template:
        return findings

    for entry in entries:
        findings.extend(_validate_entry(entry, path))

    active = [e for e in entries if e.is_active]
    if len(active) > MAX_ACTIVE_ENTRIES:
        findings.append(
            DevLogFinding(
                path=path,
                line=None,
                kind="wip_ceiling",
                message=(
                    f"{len(active)} active entries exceeds the ceiling of "
                    f"{MAX_ACTIVE_ENTRIES}. Work is being started faster than "
                    "it is finished."
                ),
                remediation="Park or ship entries before opening new ones.",
            )
        )

    # Check portfolio WIP limit if declared in header and enabled (#1465)
    if check_portfolio_wip:
        wip_match = WIP_LIMIT_HEADER.search(content)
        if wip_match:
            try:
                port_limit = int(wip_match.group("limit"))
                port_match = PORTFOLIO_HEADER.search(content)
                port_name = port_match.group("portfolio").lower() if port_match else "portfolio"
                # Only in_progress is active effort; in_review waits on CI or
                # merge (Repository_Management#1785).
                concurrent_wip = [e for e in entries if e.state == "in_progress"]
                if port_limit > 0 and len(concurrent_wip) > port_limit:
                    findings.append(
                        DevLogFinding(
                            path=path,
                            line=None,
                            kind="portfolio_wip_breach",
                            message=(
                                f"{len(concurrent_wip)} concurrent WIP entries "
                                f"(in_progress) exceeds the '{port_name}' "
                                f"portfolio cap of {port_limit}."
                            ),
                            remediation=(
                                f"Park or ship entries to bring WIP to <= {port_limit} before starting new work."
                            ),
                        )
                    )
            except ValueError:
                pass

    if len(content.encode("utf-8")) > MAX_BYTES:
        findings.append(
            DevLogFinding(
                path=path,
                line=None,
                kind="size_ceiling",
                message=f"Development log exceeds {MAX_BYTES} bytes.",
                remediation=("Move shipped entries to DEVELOPMENT_LOG_ARCHIVE_<year>.md."),
            )
        )
    return findings


def validate_repository_devlog(
    repo_root: Path,
    changed_files: Sequence[str],
    warn_only: bool = False,
) -> list[DevLogFinding]:
    """Validate a repository's canonical development log.

    When implementation files changed, the log must either be updated in the
    same commit or carry the explicit no-change escape line.
    """
    path = resolve_canonical_devlog_path(repo_root)
    if repo_root in path.parents:
        rel = path.relative_to(repo_root).as_posix()
    else:
        rel = str(path)

    if not path.is_file():
        # warn_only controls the caller's exit status, never whether a finding
        # is reported. Suppressing it here silenced exactly the repositories
        # that still need bootstrapping.
        if not requires_devlog_update(changed_files):
            return []
        return [
            DevLogFinding(
                path=path,
                line=None,
                kind="missing_devlog",
                message=f"No development log at '{rel}'.",
                remediation=(
                    "Create it from docs/templates/DEVELOPMENT_LOG.md, or run "
                    "python -m scripts.bootstrap_development_log"
                ),
            )
        ]

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return [
            DevLogFinding(
                path=path,
                line=None,
                kind="unreadable",
                message=f"Could not read '{rel}': {exc}",
                remediation="Ensure the development log is readable.",
            )
        ]

    touched = {Path(p).as_posix() for p in changed_files}
    check_wip = rel in touched or not changed_files
    findings = validate_devlog_content(content, path, check_portfolio_wip=check_wip)

    if requires_devlog_update(changed_files) and rel not in touched:
        findings.append(
            DevLogFinding(
                path=path,
                line=None,
                kind="uncommitted_devlog",
                message=f"Implementation files changed without updating '{rel}'.",
                remediation=(
                    "Refresh the affected entry's 'Last verified' and 'Next "
                    "step'. If nothing material changed, stage the log with "
                    "'No material development-log change — <reason>' recorded in "
                    "it. Staging is what satisfies this check; the presence of "
                    "that phrase from an earlier commit does not."
                ),
            )
        )
    return findings


def _entry_date(entry: Entry) -> date | None:
    """The date an entry finished: `Shipped` when present, else `Last verified`."""
    for key in ("Shipped", "Last verified"):
        match = LEADING_DATE.match(entry.fields.get(key, ""))
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                return None
    return None


def archive_terminal_entries(
    content: str, *, today: date, days: int = ARCHIVE_AFTER_DAYS
) -> tuple[str, dict[int, list[str]]]:
    """Split out shipped/abandoned entries finished more than ``days`` ago.

    Returns the remaining log and the moved entry blocks keyed by the year they
    finished, in document order. Headings and entries without a date stay put.
    """
    assert days >= 0, "days must be non-negative"
    lines = content.splitlines(keepends=True)
    kept: list[str] = []
    archived: dict[int, list[str]] = {}
    index = 0
    while index < len(lines):
        if not ENTRY_HEADING.match(lines[index].rstrip("\r\n")):
            kept.append(lines[index])
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not lines[end].startswith("#"):
            end += 1
        block = "".join(lines[index:end])
        entry = parse_entries(block)[0]
        finished = _entry_date(entry)
        if entry.state in TERMINAL_STATES and finished is not None and (today - finished).days > days:
            archived.setdefault(finished.year, []).append(block)
        else:
            kept.append(block)
        index = end
    return "".join(kept), archived


def archive_repository_devlog(repo_root: Path, *, today: date, days: int = ARCHIVE_AFTER_DAYS) -> list[Path]:
    """Move old terminal entries into ``DEVELOPMENT_LOG_ARCHIVE_<year>.md``.

    Each run's entries go above earlier runs, so archives read newest first.
    Returns the archive files written.
    """
    log_path = resolve_canonical_devlog_path(repo_root)
    remaining, archived = archive_terminal_entries(log_path.read_text(encoding="utf-8"), today=today, days=days)
    written: list[Path] = []
    for year, blocks in sorted(archived.items(), reverse=True):
        target = log_path.with_name(f"{log_path.stem}_ARCHIVE_{year}.md")
        header = f"# Development Log Archive — {year}\n\n"
        previous = target.read_text(encoding="utf-8") if target.exists() else header
        body = previous[len(header) :] if previous.startswith(header) else previous
        new = "".join(b if b.endswith("\n\n") else b + "\n" for b in blocks)
        target.write_text(header + new + body, encoding="utf-8", newline="\n")
        written.append(target)
    if archived:
        log_path.write_text(remaining, encoding="utf-8", newline="\n")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--changed", nargs="*", default=[])
    parser.add_argument("--warn-only", action="store_true")
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Move shipped/abandoned entries older than --archive-days to the "
        "yearly archive file instead of validating.",
    )
    parser.add_argument("--archive-days", type=int, default=ARCHIVE_AFTER_DAYS)
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)

    if args.archive:
        written = archive_repository_devlog(args.repo_root, today=args.today or date.today(), days=args.archive_days)
        for path in written:
            print(f"archived entries into {path}")
        if not written:
            print("Nothing to archive.")
        return 0

    findings = validate_repository_devlog(args.repo_root, args.changed, warn_only=args.warn_only)
    if not findings:
        print("Development log OK.")
        return 0

    errors = [f for f in findings if f.kind != "portfolio_wip_breach"]
    warnings = [f for f in findings if f.kind == "portfolio_wip_breach"]

    if warnings:
        print("WARNING: development log portfolio WIP breach")
        for finding in warnings:
            loc = f":{finding.line}" if finding.line else ""
            print(f"  - {finding.path.name}{loc} [{finding.kind}]: {finding.message}")
            print(f"      Remediation: {finding.remediation}")

    if errors:
        label = "WARNING" if args.warn_only else "ERROR"
        print(f"{label}: development log")
        for finding in errors:
            loc = f":{finding.line}" if finding.line else ""
            print(f"  - {finding.path.name}{loc} [{finding.kind}]: {finding.message}")
            print(f"      Remediation: {finding.remediation}")
        return 0 if args.warn_only else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
