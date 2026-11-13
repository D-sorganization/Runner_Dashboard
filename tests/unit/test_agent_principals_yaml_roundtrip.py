"""config/agent_principals.yml inline strings load as written.

An unquoted YAML scalar ends at the first `` #``, so a name such as
``Lane (desktop, #4254)`` silently loads without its tail. Every inline string
of an agent row must therefore equal the text written in the file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agent_principal_defaults import _load_config

_DEFAULTS = Path(__file__).resolve().parents[2] / "config" / "agent_principals.yml"
_ROW_START = re.compile(r"^  - id:\s+(?P<value>\S.*)$")
_ROW_FIELD = re.compile(r"^    (?P<key>[A-Za-z_]+):\s+(?P<value>\S.*)$")


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _written_agent_strings() -> list[tuple[str, str, str]]:
    """(agent id, key, text as written) for each inline field under ``agents:``."""
    written: list[tuple[str, str, str]] = []
    agent_id = ""
    in_agents = False
    for line in _DEFAULTS.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            in_agents = line.rstrip() == "agents:"
            continue
        if not in_agents:
            continue
        start = _ROW_START.match(line)
        if start:
            agent_id = _unquote(start["value"])
            written.append((agent_id, "id", agent_id))
            continue
        field = _ROW_FIELD.match(line)
        if field:
            written.append((agent_id, field["key"], _unquote(field["value"])))
    return written


@pytest.fixture()
def loaded(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(_DEFAULTS))
    return _load_config()[1]


def test_agent_inline_strings_round_trip(loaded: dict[str, dict]) -> None:
    written = _written_agent_strings()
    assert {key for _, key, _ in written} >= {"id", "name"}, "parser found no agent rows"
    truncated = [
        (agent_id, key, text, loaded[agent_id][key])
        for agent_id, key, text in written
        if key in loaded[agent_id] and loaded[agent_id][key] != text
    ]
    assert not truncated, f"quote these values in {_DEFAULTS.name}: {truncated}"


def test_openbot_barb_name_keeps_issue_reference(loaded: dict[str, dict]) -> None:
    assert loaded["openbot-barb"]["name"].endswith("#4254)")
