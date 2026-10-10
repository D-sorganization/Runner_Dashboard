"""Render the fleet tool table in ``docs/agents/connect.md`` from ``fleet_tools.COMMANDS``.

Usage::

    python -m scripts.render_fleet_tools_table --write   # rewrite the generated block
    python -m scripts.render_fleet_tools_table --check   # exit 1 when the block drifted

Authority: Runner_Dashboard#1802 (step 2).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FLEET_DIR = _ROOT / "clients" / "fleet"
if str(_FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(_FLEET_DIR))

from fleet_tools import COMMANDS, Command  # noqa: E402

CONNECT_MD = _ROOT / "docs" / "agents" / "connect.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED: fleet-tools -->"
END_MARKER = "<!-- END GENERATED: fleet-tools -->"
HEADERS = ("MCP tool", "CLI subcommand", "Description", "Required args")


def _cell(text: str) -> str:
    """Make ``text`` safe for one Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def _row(command: Command) -> tuple[str, ...]:
    tool = f"`{command.tool}`" if command.tool else "-"
    required = ", ".join(f"`{name}`" for name in command.required) or "-"
    return (tool, f"`fleetctl {command.cli}`", _cell(command.description), required)


def render_table(commands: Iterable[Command]) -> str:
    """Return the Markdown table for ``commands`` (no trailing newline).

    Columns are padded to equal width so the output is already Prettier-clean.

    Postcondition: one data row per command, in the given order.
    """
    rows = [HEADERS, *(_row(c) for c in commands)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(HEADERS))]

    def line(cells: Iterable[str]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)) + " |"

    divider = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([line(rows[0]), divider, *(line(r) for r in rows[1:])])


def splice(document: str, table: str) -> str:
    """Return ``document`` with the marked block replaced by ``table``.

    Raises:
        ValueError: when the begin/end markers are missing or out of order.
    """
    start = document.find(BEGIN_MARKER)
    end = document.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"{BEGIN_MARKER} / {END_MARKER} markers not found in order")
    head = document[: start + len(BEGIN_MARKER)]
    return f"{head}\n\n{table}\n\n{document[end:]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite the generated block in place")
    mode.add_argument("--check", action="store_true", help="exit 1 if the block differs from COMMANDS")
    args = parser.parse_args(argv)
    current = CONNECT_MD.read_text(encoding="utf-8")
    updated = splice(current, render_table(COMMANDS))
    if args.check:
        if updated != current:
            sys.stderr.write("connect.md tool table is stale; run --write\n")
            return 1
        return 0
    if updated != current:
        CONNECT_MD.write_text(updated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
