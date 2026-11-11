"""Drift checks for generated MCP tool inventory docs and llms.txt link targets (#4304)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README_MD = REPO_ROOT / "clients" / "fleet" / "README.md"
CONNECT_MD = REPO_ROOT / "docs" / "agents" / "connect.md"
LLMS_TXT = REPO_ROOT / "llms.txt"

if str(REPO_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO_ROOT))
_FLEET = REPO_ROOT / "clients" / "fleet"
if str(_FLEET) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_FLEET))

from fleet_tools import BY_TOOL, COMMANDS  # noqa: E402
from mcp_auth_scopes import METHOD_AUTH_SCOPES, auth_scope_for_fleet_tool  # noqa: E402

from scripts import rd_threads_mcp  # noqa: E402
from scripts.render_mcp_tool_inventory import (  # noqa: E402
    CONNECT_INVENTORY_BEGIN,
    CONNECT_INVENTORY_END,
    README_INVENTORY_BEGIN,
    README_INVENTORY_END,
    fleet_inventory_rows,
    rd_threads_inventory_rows,
    updated_documents,
)


def test_every_fleet_mcp_tool_has_auth_scope_mapping() -> None:
    methods = {cmd.method for cmd in COMMANDS if cmd.tool}
    missing = sorted(methods - set(METHOD_AUTH_SCOPES))
    assert not missing, f"METHOD_AUTH_SCOPES missing methods: {missing}"
    for cmd in COMMANDS:
        if not cmd.tool:
            continue
        auth_scope_for_fleet_tool(cmd)


def test_rd_threads_auth_scopes_cover_tools() -> None:
    assert set(rd_threads_mcp.TOOL_AUTH_SCOPES) == set(rd_threads_mcp.TOOLS)


def test_inventory_rows_match_registered_tools() -> None:
    fleet_names = [row.name for row in fleet_inventory_rows()]
    assert fleet_names == [cmd.tool for cmd in COMMANDS if cmd.tool]
    assert set(fleet_names) == set(BY_TOOL)
    rd_names = [row.name for row in rd_threads_inventory_rows()]
    assert rd_names == list(rd_threads_mcp.TOOLS)


def test_readme_and_connect_inventory_blocks_are_fresh() -> None:
    readme_updated, connect_updated = updated_documents()
    assert readme_updated == README_MD.read_text(encoding="utf-8")
    assert connect_updated == CONNECT_MD.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path,begin,end",
    (
        (README_MD, README_INVENTORY_BEGIN, README_INVENTORY_END),
        (CONNECT_MD, CONNECT_INVENTORY_BEGIN, CONNECT_INVENTORY_END),
    ),
)
def test_inventory_markers_present(path: Path, begin: str, end: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert begin in text and end in text


def test_llms_txt_exists_and_links_resolve() -> None:
    assert LLMS_TXT.is_file()
    text = LLMS_TXT.read_text(encoding="utf-8")
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    for match in pattern.finditer(text):
        target = match.group(2)
        if target.startswith("http://") or target.startswith("https://"):
            continue
        path_part = target.split("#", 1)[0]
        resolved = (REPO_ROOT / path_part).resolve()
        assert resolved.is_file(), f"llms.txt link missing file: {target}"


def test_llms_txt_points_at_core_agent_surfaces() -> None:
    text = LLMS_TXT.read_text(encoding="utf-8")
    assert "docs/agents/connect.md" in text
    assert "openapi.json" in text
    assert "clients/fleet/README.md" in text


def test_readme_inventory_mentions_both_servers() -> None:
    text = README_MD.read_text(encoding="utf-8")
    section = text.split(README_INVENTORY_BEGIN, 1)[1].split(README_INVENTORY_END, 1)[0]
    assert "fleet_mcp.py" in section and "rd_threads_mcp.py" in section
