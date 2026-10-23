"""Project adapters retire legacy quality callbacks, preserving other hooks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared_scripts.agent_hook_adapters import (
    SUPPORTED_TARGETS,
    bridge_command,
    bridge_command_git_root,
    check_adapters,
    generate_adapters,
)


@pytest.mark.unit
def test_project_quality_hooks_retire_without_losing_other_settings(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "fleet-hooks").mkdir()
    (tmp_path / "fleet-hooks/manifest.yml").write_bytes((root / "fleet-hooks/manifest.yml").read_bytes())
    paths = [
        ".cursor/hooks.json",
        ".claude/settings.json",
        ".codex/hooks.json",
        ".agents/hooks.json",
        ".gemini/settings.json",
    ]
    for index, name in enumerate(paths):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = json.loads((root / name).read_text())
        doc["operatorSetting"] = {"preserved": True}
        # Add an unrelated callback to the same event as the retiring hook.
        hooks = doc.get("hooks", doc.get("fleet-agent-check"))
        event = next(k for k in hooks if k not in {"sessionStart"})
        mode = ["cursor-stop", "claude-stop", "codex-stop", "antigravity-stop", "gemini-after-tool"][index]
        legacy = {"type": "command", "command": bridge_command(mode) if index < 2 else bridge_command_git_root(mode)}
        wrapped = name in {".claude/settings.json", ".codex/hooks.json", ".gemini/settings.json"}
        hooks[event].append({"hooks": [legacy]} if wrapped else legacy)
        callback = {"command": "operator-callback", "type": "command"}
        hooks[event].append(
            {"hooks": [callback]}
            if name in {".claude/settings.json", ".codex/hooks.json", ".gemini/settings.json"}
            else callback
        )
        target.write_text(json.dumps(doc))
    generate_adapters(tmp_path, targets=SUPPORTED_TARGETS)
    for name in paths:
        text = (tmp_path / name).read_text()
        assert "agent-check-hook-bridge.py" not in text
        assert "operator-callback" in text
        assert json.loads(text)["operatorSetting"] == {"preserved": True}
    assert "agent-bus-hook-bridge.py" in (tmp_path / ".cursor/hooks.json").read_text()
    assert "pre_push_agent_check.py" in (tmp_path / "fleet-hooks/pre-push.sample").read_text()
    assert check_adapters(tmp_path, targets=SUPPORTED_TARGETS) == []
    before = {name: (tmp_path / name).read_bytes() for name in paths}
    generate_adapters(tmp_path, targets=SUPPORTED_TARGETS)
    assert before == {name: (tmp_path / name).read_bytes() for name in paths}
