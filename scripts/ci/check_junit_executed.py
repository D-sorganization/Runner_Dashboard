#!/usr/bin/env python3
"""Fail a test lane that went green without executing anything.

Reads a pytest JUnit XML report and exits non-zero when the report is
missing or unparseable, selected zero cases, or every selected case was
skipped. Otherwise prints the executed case IDs (and appends them to
``$GITHUB_STEP_SUMMARY`` when set) so a run records what it exercised.

Tracking: issue #3776.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Report:
    executed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def parse_report(path: Path) -> Report:
    root = ET.parse(path).getroot()
    report = Report()
    for case in root.iter("testcase"):
        case_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("skipped") is not None:
            report.skipped.append(case_id)
        else:
            report.executed.append(case_id)
    return report


def check(path: Path, min_executed: int = 1) -> tuple[int, str]:
    if not path.is_file():
        return 2, f"JUnit report not found: {path}"
    try:
        report = parse_report(path)
    except ET.ParseError as exc:
        return 2, f"JUnit report unparseable: {path}: {exc}"
    total = len(report.executed) + len(report.skipped)
    if total == 0:
        return 1, "No test cases were selected; refusing to report green."
    if len(report.executed) < min_executed:
        return 1, (
            f"Only {len(report.executed)} of {total} selected cases executed "
            f"({len(report.skipped)} skipped); expected at least {min_executed}."
        )
    lines = [f"Executed {len(report.executed)} case(s), skipped {len(report.skipped)}:"]
    lines += [f"- {case_id}" for case_id in report.executed]
    return 0, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--min-executed", type=int, default=1)
    args = parser.parse_args(argv)
    code, message = check(args.report, args.min_executed)
    print(message, file=sys.stderr if code else sys.stdout)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(message + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
