"""Tests for external agent connection documentation invariants (SC-F5, Issue #1334).

Verifies that:
1. One dedicated guide page exists per external agent client:
   - `docs/agents/claude.md` (Claude Code & Claude Cowork)
   - `docs/agents/codex.md` (Codex CLI)
   - `docs/agents/grok.md` (Grok Bot & local-exec curl)
2. `docs/agents/connect.md` indexes all client guides and all relative links resolve.
3. Every MCP tool defined in `clients.fleet.fleet_tools.COMMANDS` is documented in `connect.md`.
4. `connect.md` includes the SC-F3 classified error envelope troubleshooting table.
5. `docs/staff-hub.md` references the agent connection documentation.
"""

from __future__ import annotations

import re
from pathlib import Path

from clients.fleet.fleet_tools import COMMANDS

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_AGENTS_DIR = REPO_ROOT / "docs" / "agents"
CONNECT_MD = DOCS_AGENTS_DIR / "connect.md"
CLAUDE_MD = DOCS_AGENTS_DIR / "claude.md"
CODEX_MD = DOCS_AGENTS_DIR / "codex.md"
GROK_MD = DOCS_AGENTS_DIR / "grok.md"
STAFF_HUB_MD = REPO_ROOT / "docs" / "staff-hub.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"Expected document does not exist: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_dedicated_client_pages_exist() -> None:
    """Each external agent client has a dedicated connection guide."""
    assert CLAUDE_MD.is_file(), "docs/agents/claude.md must exist"
    assert CODEX_MD.is_file(), "docs/agents/codex.md must exist"
    assert GROK_MD.is_file(), "docs/agents/grok.md must exist"


def test_claude_guide_contents() -> None:
    """Claude guide covers both Claude Code and Claude Cowork with MCP and scopes."""
    text = _read(CLAUDE_MD)
    lower = text.lower()

    assert "claude code" in lower
    assert "claude cowork" in lower or "cowork" in lower
    assert "fleet_mcp.py" in text
    assert "/api/admin/principals" in text
    assert "agent-claude" in text
    # Minimum required scopes from SC-F1
    assert "staff.chat" in text
    assert "staff.read" in text
    assert "staff.dispatch" in text
    assert "barb" in lower


def test_codex_guide_contents() -> None:
    """Codex guide covers config.toml MCP configuration and talking to Barb."""
    text = _read(CODEX_MD)
    lower = text.lower()

    assert "config.toml" in text
    assert "mcp_servers.fleet" in text
    assert "fleet_mcp.py" in text
    assert "agent-codex" in text
    assert "staff.chat" in text
    assert "barb" in lower


def test_grok_guide_contents() -> None:
    """Grok guide covers local-exec curl recipes, active roles, and connector path."""
    text = _read(GROK_MD)
    lower = text.lower()

    assert "curl" in lower
    assert "/api/v1/staff" in text
    assert "barb" in lower
    assert "orchestrator" in lower
    assert "idempotency-key" in lower or "idempotency" in lower
    # ADR 0007: cloud-hosted clients are unsupported; no Funnel/connector promise.
    assert "0007-agent-client-ingress-local-only.md" in text
    assert "not supported" in lower
    assert "will route through the authenticated funnel" not in lower


def test_agent_guides_do_not_promise_funnel_or_cloud_ingress() -> None:
    """No agent guide promises Funnel or connector ingress (ADR 0007)."""
    for path in (CONNECT_MD, CLAUDE_MD, CODEX_MD, GROK_MD):
        lower = _read(path).lower()
        assert "authenticated funnel" not in lower, f"{path.name} promises Funnel ingress"
        assert "funnel connector" not in lower, f"{path.name} promises a connector path"


def test_connect_md_has_supported_clients_and_networks_matrix() -> None:
    """connect.md carries the four-row client/network matrix with cloud-only unsupported."""
    text = _read(CONNECT_MD)
    assert "## Supported Clients and Networks" in text
    assert "0007-agent-client-ingress-local-only.md" in text
    section = text.split("## Supported Clients and Networks", 1)[1].split("\n## ", 1)[0]
    for column in ("Endpoint", "Transport", "Identity / token", "Network prerequisite"):
        assert column in section, f"matrix missing column '{column}'"
    rows = {
        line.split("|")[1].strip().lower(): line.lower()
        for line in section.splitlines()
        if line.startswith("|") and not line.startswith("| :") and not line.startswith("| -")
    }
    for client in ("local process", "enrolled remote computer", "desktop connector", "cloud-only client"):
        assert any(key.startswith(client) for key in rows), f"matrix missing row '{client}'"
    cloud_row = next(row for key, row in rows.items() if key.startswith("cloud-only client"))
    assert "not supported (adr 0007)" in cloud_row


def test_connect_md_indexes_all_client_guides() -> None:
    """connect.md links to claude.md, codex.md, and grok.md."""
    text = _read(CONNECT_MD)
    assert "claude.md" in text, "connect.md must link to claude.md"
    assert "codex.md" in text, "connect.md must link to codex.md"
    assert "grok.md" in text, "connect.md must link to grok.md"


def test_connect_md_all_internal_markdown_links_resolve() -> None:
    """Every relative markdown file link in connect.md resolves to an existing file."""
    text = _read(CONNECT_MD)
    # Find links like [text](relative_path.md)
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)(?:#[^)]*)?\)")
    for match in link_pattern.finditer(text):
        link_target = match.group(2)
        if link_target.startswith("http://") or link_target.startswith("https://"):
            continue
        resolved = (DOCS_AGENTS_DIR / link_target).resolve()
        assert resolved.is_file(), (
            f"Broken relative link in connect.md: '{link_target}' resolves to missing '{resolved}'"
        )


def test_connect_md_documents_all_mcp_tools() -> None:
    """Every command in fleet_tools.py COMMANDS is documented in connect.md."""
    text = _read(CONNECT_MD)
    missing_tools: list[str] = []
    for cmd in COMMANDS:
        if not cmd.tool:
            continue
        tool_name = cmd.tool
        if f"`{tool_name}`" not in text:
            missing_tools.append(tool_name)
    assert not missing_tools, f"connect.md missing documentation for MCP tools: {missing_tools}"


def test_connect_md_troubleshooting_table_has_sc_f3_codes() -> None:
    """connect.md includes a troubleshooting guide keyed by SC-F3 error codes."""
    text = _read(CONNECT_MD)
    lower = text.lower()

    expected_codes = (
        "unauthorized",
        "forbidden",
        "not_found",
        "conflict",
        "validation_error",
        "rate_limited",
        "service_unavailable",
    )
    for code in expected_codes:
        assert code in lower, f"connect.md troubleshooting table must cover error code '{code}'"


def test_staff_hub_md_links_to_agent_connect_guide() -> None:
    """docs/staff-hub.md references the agent connection documentation."""
    text = _read(STAFF_HUB_MD)
    assert "connect.md" in text or "docs/agents/" in text, "docs/staff-hub.md must link to docs/agents/connect.md"
