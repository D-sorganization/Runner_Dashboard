#!/usr/bin/env python3
"""Reject Closes #<board> and Fixes #<board> references to coordination board (issue #4574)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from scripts.automerge_guard import _closing_board_reasons


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--body", help="PR body text to validate")
    args = parser.parse_args(argv)

    body = args.body
    if body is None:
        if raw_json := os.environ.get("PR_BODY_JSON"):
            try:
                body = json.loads(raw_json)
            except Exception:
                body = raw_json
        else:
            body = os.environ.get("FLEET_PR_BODY", "")

    reasons = _closing_board_reasons(body or "")
    if reasons:
        for r in reasons:
            sys.stderr.write(f"REJECT: {r}\n")
        return 1
    sys.stdout.write("PASS: no coordination board closing references found\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
