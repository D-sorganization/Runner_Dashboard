"""Generate project adapters while delegating quality hooks to protected globals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from src.contracts import require

BRIDGE_REL = "scripts/agent-check-hook-bridge.py"
AGENT_BUS_BRIDGE_REL = "scripts/agent-bus-hook-bridge.py"
CURSOR_HOOK_VERSION = 1
ANTIGRAVITY_HOOK_GROUP = "fleet-agent-check"
SUPPORTED_TARGETS = frozenset({"cursor", "claude", "codex", "antigravity", "gemini", "pre-push"})


class AdapterError(ValueError):
    """Raised when manifest or adapter validation fails."""


@dataclass(frozen=True)
class WriteResult:
    path: str
    changed: bool


def load_manifest(root: Path) -> dict[str, Any]:
    path = root / "fleet-hooks" / "manifest.yml"
    require(path.is_file(), f"missing manifest: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    require(isinstance(data, dict), "manifest must be a mapping")
    require(int(data.get("schema_version", 0)) == 1, "unsupported manifest schema_version")
    return cast(dict[str, Any], data)


def default_timeout(manifest: dict[str, Any]) -> int:
    defaults = manifest.get("defaults") or {}
    return int(defaults.get("timeout_seconds") or 120)


def bridge_command(mode: str) -> str:
    return f"python3 {BRIDGE_REL} {mode}"


def bridge_command_git_root(mode: str) -> str:
    """Resolve the bridge from ``git rev-parse --show-toplevel`` (Codex cwd)."""
    return f'bash -lc \'root="$(git rev-parse --show-toplevel)"; exec python3 "$root/{BRIDGE_REL}" {mode}\'"'


def retire_project_quality_hooks(existing: dict[str, Any]) -> dict[str, Any]:
    """Remove only exact generated quality commands; preserve unrelated callbacks.

    Provider project/global hooks merge. Quality enforcement must execute from
    the administrator-owned global runtime rather than writable checkout code.
    Agent-bus callbacks and Git pre-push validation remain separate.
    """
    modes = (
        "cursor-stop",
        "claude-stop",
        "codex-stop",
        "antigravity-post-tool",
        "antigravity-stop",
        "gemini-after-tool",
        "gemini-after-agent",
    )
    retired = {command for mode in modes for command in (bridge_command(mode), bridge_command_git_root(mode))}
    removed = object()

    def clean(value: Any) -> Any:
        if isinstance(value, list):
            items = [clean(item) for item in value]
            return [item for item in items if item is not removed]
        if isinstance(value, dict):
            if isinstance(value.get("command"), str) and value["command"] in retired:
                return removed
            result = {key: clean(item) for key, item in value.items()}
            # Drop event wrappers emptied by removal, retaining unrelated fields
            # on all non-wrapper settings objects.
            if isinstance(value.get("hooks"), list) and value["hooks"] and result["hooks"] == []:
                return removed
            return result
        return value

    return cast(dict[str, Any], clean(existing))


def _agent_bus_hook(mode: str, timeout_seconds: int) -> dict[str, Any]:
    return {
        "type": "command",
        "command": f"python3 {AGENT_BUS_BRIDGE_REL} {mode}",
        "timeout": timeout_seconds,
    }


def generate_cursor_hooks(root: Path, *, timeout_seconds: int) -> dict[str, Any]:
    doc = retire_project_quality_hooks(_read_json_object(root / ".cursor/hooks.json"))
    doc.setdefault("version", CURSOR_HOOK_VERSION)
    hooks = doc.setdefault("hooks", {})
    hooks.setdefault("stop", [])
    starts = hooks.setdefault("sessionStart", [])
    bus = _agent_bus_hook("session-start", timeout_seconds)
    if not any(isinstance(item, dict) and item.get("command") == bus["command"] for item in starts):
        starts.append(bus)
    return doc


def merge_claude_settings(existing: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    require(isinstance(existing, dict), "settings must be an object")
    merged = retire_project_quality_hooks(existing)
    merged.setdefault("hooks", {}).setdefault("Stop", [])
    return merged


def validate_cursor_hooks(doc: Any) -> None:
    require(isinstance(doc, dict), "cursor hooks must be an object")
    require(doc.get("version") == CURSOR_HOOK_VERSION, "cursor hooks.version must be 1")
    hooks = doc.get("hooks")
    require(isinstance(hooks, dict), "cursor hooks.hooks must be an object")
    stop = hooks.get("stop")
    require(isinstance(stop, list), "cursor hooks.stop must be a list")

    for item in stop:
        require(isinstance(item, dict), "cursor stop entry must be an object")
        require(item.get("type") in {None, "command"}, "cursor stop must be command type")
        require(isinstance(item.get("command"), str) and item["command"], "command required")


def generate_codex_hooks(root: Path, *, timeout_seconds: int) -> dict[str, Any]:
    doc = retire_project_quality_hooks(_read_json_object(root / ".codex/hooks.json"))
    if doc.get("description", "").startswith("Fleet agent-check hooks (Codex CLI pin "):
        doc["description"] = "Fleet quality hooks are installed in the protected global profile."
    doc.setdefault("hooks", {}).setdefault("Stop", [])
    return doc


def generate_antigravity_hooks(root: Path, *, timeout_seconds: int) -> dict[str, Any]:
    doc = retire_project_quality_hooks(_read_json_object(root / ".agents/hooks.json"))
    group = doc.setdefault(ANTIGRAVITY_HOOK_GROUP, {})
    group.setdefault("PostToolUse", [])
    group.setdefault("Stop", [])
    return doc


def merge_gemini_settings(existing: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    require(isinstance(existing, dict), "gemini settings must be an object")
    merged = retire_project_quality_hooks(existing)
    hooks = merged.setdefault("hooks", {})
    hooks.setdefault("AfterTool", [])
    hooks.setdefault("AfterAgent", [])
    return merged


def generate_pre_push_sample() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'root="$(git rev-parse --show-toplevel)"\n'
        'python3 "$root/scripts/agent-bus-hook-bridge.py" pre-push || true\n'
        'exec python3 "$root/scripts/pre_push_agent_check.py"\n'
    )


def validate_codex_hooks(doc: Any) -> None:
    require(isinstance(doc, dict), "codex hooks must be an object")
    hooks = doc.get("hooks")
    require(isinstance(hooks, dict), "codex hooks.hooks required")
    require(isinstance(hooks.get("Stop"), list), "codex hooks.Stop must be a list")


def validate_antigravity_hooks(doc: Any) -> None:
    require(isinstance(doc, dict), "antigravity hooks must be an object")
    group = doc.get(ANTIGRAVITY_HOOK_GROUP)
    require(isinstance(group, dict), "antigravity hook group required")
    require(isinstance(group.get("PostToolUse"), list), "PostToolUse must be a list")
    require(isinstance(group.get("Stop"), list), "Stop must be a list")


def validate_gemini_settings(doc: Any) -> None:
    require(isinstance(doc, dict), "gemini settings must be an object")
    hooks = doc.get("hooks")
    require(isinstance(hooks, dict), "gemini hooks required")
    after_tool = hooks.get("AfterTool")
    require(isinstance(after_tool, list), "AfterTool required")
    after_agent = hooks.get("AfterAgent")
    require(isinstance(after_agent, list), "AfterAgent required")


def validate_claude_settings(doc: Any) -> None:
    require(isinstance(doc, dict), "claude settings must be an object")
    hooks = doc.get("hooks")
    if hooks is None:
        return
    require(isinstance(hooks, dict), "claude hooks must be an object")
    stop = hooks.get("Stop")
    if stop is None:
        return
    require(isinstance(stop, list), "claude Stop must be a list")
    for entry in stop:
        require(isinstance(entry, dict), "claude Stop entry must be an object")
        inner = entry.get("hooks")
        require(isinstance(inner, list), "claude Stop.hooks must be a list")
        require(bool(inner), "claude Stop.hooks required")
        for hook in inner:
            require(isinstance(hook, dict), "claude hook must be an object")
            require(hook.get("type") == "command", "claude hook type must be command")
            require(isinstance(hook.get("command"), str), "claude hook command required")


def _write_json(path: Path, doc: dict[str, Any]) -> WriteResult:
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    changed = not path.is_file() or path.read_text(encoding="utf-8") != text
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return WriteResult(path.as_posix(), changed)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        require(False, f"{path} must be a JSON object")
    return cast(dict[str, Any], parsed)


def generate_adapters(
    root: Path,
    *,
    targets: frozenset[str],
    write: bool = True,
) -> dict[str, WriteResult]:
    unknown = targets - SUPPORTED_TARGETS
    require(not unknown, f"unknown adapter targets: {sorted(unknown)}")
    manifest = load_manifest(root)
    timeout = default_timeout(manifest)
    results: dict[str, WriteResult] = {}
    if "cursor" in targets:
        doc = generate_cursor_hooks(root, timeout_seconds=timeout)
        validate_cursor_hooks(doc)
        path = root / ".cursor/hooks.json"
        results["cursor"] = _write_json(path, doc) if write else WriteResult(path.as_posix(), _differs(path, doc))
    if "claude" in targets:
        settings_path = root / ".claude/settings.json"
        existing = _read_json_object(settings_path) if settings_path.is_file() else {}
        if not existing:
            existing = {"outputStyle": "Proactive"}
        merged = merge_claude_settings(existing, timeout_seconds=timeout)
        validate_claude_settings(merged)
        text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
        changed = not settings_path.is_file() or settings_path.read_text(encoding="utf-8") != text
        if write and changed:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(text, encoding="utf-8")
        results["claude"] = WriteResult(settings_path.as_posix(), changed)
    if "codex" in targets:
        doc = generate_codex_hooks(root, timeout_seconds=timeout)
        validate_codex_hooks(doc)
        path = root / ".codex/hooks.json"
        results["codex"] = _write_json(path, doc) if write else WriteResult(path.as_posix(), _differs(path, doc))
    if "antigravity" in targets:
        doc = generate_antigravity_hooks(root, timeout_seconds=timeout)
        validate_antigravity_hooks(doc)
        path = root / ".agents/hooks.json"
        results["antigravity"] = _write_json(path, doc) if write else WriteResult(path.as_posix(), _differs(path, doc))
    if "gemini" in targets:
        settings_path = root / ".gemini/settings.json"
        existing = _read_json_object(settings_path) if settings_path.is_file() else {}
        merged = merge_gemini_settings(existing, timeout_seconds=timeout)
        validate_gemini_settings(merged)
        text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
        changed = not settings_path.is_file() or settings_path.read_text(encoding="utf-8") != text
        if write and changed:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(text, encoding="utf-8")
        results["gemini"] = WriteResult(settings_path.as_posix(), changed)
    if "pre-push" in targets:
        path = root / "fleet-hooks/pre-push.sample"
        text = generate_pre_push_sample()
        changed = not path.is_file() or path.read_text(encoding="utf-8") != text
        if write and changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        results["pre-push"] = WriteResult(path.as_posix(), changed)
    return results


def _differs(path: Path, doc: dict[str, Any]) -> bool:
    expected = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    return not path.is_file() or path.read_text(encoding="utf-8") != expected


def check_adapters(root: Path, *, targets: frozenset[str]) -> list[str]:
    manifest = load_manifest(root)
    timeout = default_timeout(manifest)
    drift: list[str] = []
    if "cursor" in targets:
        path = root / ".cursor/hooks.json"
        doc = generate_cursor_hooks(root, timeout_seconds=timeout)
        if _differs(path, doc):
            drift.append(path.as_posix())
    if "claude" in targets:
        path = root / ".claude/settings.json"
        existing = _read_json_object(path) if path.is_file() else {"outputStyle": "Proactive"}
        doc = merge_claude_settings(existing, timeout_seconds=timeout)
        expected = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            drift.append(path.as_posix())
    if "codex" in targets:
        path = root / ".codex/hooks.json"
        doc = generate_codex_hooks(root, timeout_seconds=timeout)
        if _differs(path, doc):
            drift.append(path.as_posix())
    if "antigravity" in targets:
        path = root / ".agents/hooks.json"
        doc = generate_antigravity_hooks(root, timeout_seconds=timeout)
        if _differs(path, doc):
            drift.append(path.as_posix())
    if "gemini" in targets:
        path = root / ".gemini/settings.json"
        existing = _read_json_object(path) if path.is_file() else {}
        doc = merge_gemini_settings(existing, timeout_seconds=timeout)
        expected = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            drift.append(path.as_posix())
    if "pre-push" in targets:
        path = root / "fleet-hooks/pre-push.sample"
        if not path.is_file() or path.read_text(encoding="utf-8") != generate_pre_push_sample():
            drift.append(path.as_posix())
    return drift
