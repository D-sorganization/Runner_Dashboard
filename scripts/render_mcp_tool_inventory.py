"""Render MCP tool inventory blocks in agent docs from registered fleet and rd-threads tools.

Usage::

    python -m scripts.render_mcp_tool_inventory --write   # rewrite generated blocks
    python -m scripts.render_mcp_tool_inventory --check   # exit 1 when docs drifted
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FLEET_DIR = _ROOT / "clients" / "fleet"
if str(_FLEET_DIR) not in sys.path:
    sys.path.insert(0, str(_FLEET_DIR))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fleet_tools import COMMANDS  # noqa: E402
from mcp_auth_scopes import auth_scope_for_fleet_tool  # noqa: E402

from scripts import rd_threads_mcp  # noqa: E402

README_MD = _ROOT / "clients" / "fleet" / "README.md"
CONNECT_MD = _ROOT / "docs" / "agents" / "connect.md"

README_INVENTORY_BEGIN = "<!-- BEGIN GENERATED: mcp-tool-inventory -->"
README_INVENTORY_END = "<!-- END GENERATED: mcp-tool-inventory -->"
CONNECT_INVENTORY_BEGIN = "<!-- BEGIN GENERATED: mcp-tool-inventory -->"
CONNECT_INVENTORY_END = "<!-- END GENERATED: mcp-tool-inventory -->"

INVENTORY_HEADERS = ("MCP tool", "Description", "Auth scope")


@dataclass(frozen=True)
class InventoryRow:
    name: str
    description: str
    auth_scope: str


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|").replace("*", "\\*")


def _one_line(description: str) -> str:
    text = " ".join(description.split())
    if len(text) <= 160:
        return text
    cut = text[:157].rsplit(" ", 1)[0]
    return f"{cut}…"


def fleet_inventory_rows() -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for cmd in COMMANDS:
        if not cmd.tool:
            continue
        rows.append(
            InventoryRow(
                name=cmd.tool,
                description=_one_line(cmd.description),
                auth_scope=auth_scope_for_fleet_tool(cmd),
            )
        )
    return rows


def rd_threads_inventory_rows() -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for name, (description, _required) in rd_threads_mcp.TOOLS.items():
        scope = rd_threads_mcp.TOOL_AUTH_SCOPES.get(name)
        if scope is None:
            raise KeyError(f"rd_threads_mcp.TOOL_AUTH_SCOPES missing {name!r}")
        rows.append(InventoryRow(name=name, description=_one_line(description), auth_scope=scope))
    return rows


def render_markdown_table(rows: Sequence[InventoryRow]) -> str:
    body = [(f"`{row.name}`", _cell(row.description), f"`{row.auth_scope}`") for row in rows]
    table_rows = [INVENTORY_HEADERS, *body]
    widths = [max(len(row[i]) for row in table_rows) for i in range(len(INVENTORY_HEADERS))]

    def line(cells: Iterable[str]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)) + " |"

    divider = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([line(table_rows[0]), divider, *(line(r) for r in table_rows[1:])])


def render_readme_inventory(fleet_rows: Sequence[InventoryRow], rd_rows: Sequence[InventoryRow]) -> str:
    parts = [
        "## MCP tool inventory",
        "",
        "Generated from `clients/fleet/fleet_tools.py` and `scripts/rd_threads_mcp.py`; "
        "run `python -m scripts.render_mcp_tool_inventory --write` after changing tools.",
        "",
        "### `fleet_mcp.py` (`clients/fleet/fleet_mcp.py`)",
        "",
        render_markdown_table(fleet_rows),
        "",
        "### `rd_threads_mcp.py` (`scripts/rd_threads_mcp.py`)",
        "",
        render_markdown_table(rd_rows),
    ]
    return "\n".join(parts)


def render_connect_inventory(fleet_rows: Sequence[InventoryRow], rd_rows: Sequence[InventoryRow]) -> str:
    parts = [
        "Compact inventory (name, one-line description, bearer-token scope). "
        "Regenerate with `python -m scripts.render_mcp_tool_inventory --write`.",
        "",
        "#### `fleet_mcp.py`",
        "",
        render_markdown_table(fleet_rows),
        "",
        "#### `rd_threads_mcp.py`",
        "",
        render_markdown_table(rd_rows),
    ]
    return "\n".join(parts)


def splice(document: str, begin: str, end: str, block: str) -> str:
    start = document.find(begin)
    stop = document.find(end)
    if start == -1 or stop == -1 or stop < start:
        raise ValueError(f"{begin} / {end} markers not found in order")
    head = document[: start + len(begin)]
    return f"{head}\n\n{block}\n\n{document[stop:]}"


def updated_documents() -> tuple[str, str]:
    fleet_rows = fleet_inventory_rows()
    rd_rows = rd_threads_inventory_rows()
    readme = README_MD.read_text(encoding="utf-8")
    connect = CONNECT_MD.read_text(encoding="utf-8")
    readme = splice(readme, README_INVENTORY_BEGIN, README_INVENTORY_END, render_readme_inventory(fleet_rows, rd_rows))
    connect = splice(
        connect, CONNECT_INVENTORY_BEGIN, CONNECT_INVENTORY_END, render_connect_inventory(fleet_rows, rd_rows)
    )
    return readme, connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite generated blocks in place")
    mode.add_argument("--check", action="store_true", help="exit 1 if docs differ from registered tools")
    args = parser.parse_args(argv)

    readme_current = README_MD.read_text(encoding="utf-8")
    connect_current = CONNECT_MD.read_text(encoding="utf-8")
    readme_updated, connect_updated = updated_documents()

    drift = []
    if readme_updated != readme_current:
        drift.append(str(README_MD.relative_to(_ROOT)))
    if connect_updated != connect_current:
        drift.append(str(CONNECT_MD.relative_to(_ROOT)))

    if args.check:
        for path in drift:
            sys.stderr.write(f"{path}: MCP tool inventory is stale; run --write\n")
        return 1 if drift else 0

    if readme_updated != readme_current:
        README_MD.write_text(readme_updated, encoding="utf-8", newline="\n")
    if connect_updated != connect_current:
        CONNECT_MD.write_text(connect_updated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
