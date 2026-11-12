#!/usr/bin/env python3
"""Read-only provider messaging capability probe for the postman relay (#3234).

Reports how each subscription CLI on this host can receive relay traffic:
Claude Code via local push (cross-session socket / Channels), Codex and agy via
hooks or hub poll. No network calls, no model calls, no writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from staff.redaction import redact_sensitive_content  # noqa: E402

CLAUDE_PUSH_ENV = ("CLAUDE_CODE_SSE_PORT",)
CODEX_HOOK_EVENTS = ("UserPromptSubmit", "Stop")
AGY_HOOK_EVENTS = ("AfterAgent", "AfterTool", "Stop")
AGY_HOOK_GROUP = "fleet-agent-check"


class ProbeContractError(ValueError):
    """Pre/post condition for probe assembly failed."""


@dataclass(frozen=True)
class ProviderCaps:
    delivery: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.delivery not in {"push", "hook", "poll"}:
            raise ProbeContractError(f"unknown delivery mode: {self.delivery}")
        if not self.evidence:
            raise ProbeContractError("evidence required")


def sanitize_text(text: str) -> str:
    """Redact secret-shaped substrings from probe output."""
    return redact_sensitive_content(text)


def _env_flag(env: Mapping[str, str], name: str) -> bool:
    return bool(env.get(name, "").strip())


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _hook_commands(doc: Mapping[str, Any], *event_names: str) -> list[str]:
    found: list[str] = []
    hooks = doc.get("hooks")
    if isinstance(hooks, dict):
        for name in event_names:
            items = hooks.get(name)
            if isinstance(items, list) and items:
                found.append(name)
    group = doc.get(AGY_HOOK_GROUP)
    if isinstance(group, dict):
        for name in event_names:
            items = group.get(name)
            if isinstance(items, list) and items:
                found.append(f"{AGY_HOOK_GROUP}.{name}")
    return found


def _claude_config_dir(env: Mapping[str, str], override: Path | None) -> Path:
    if override is not None:
        return override
    raw = (env.get("CLAUDE_CONFIG_DIR") or "").strip() or "~/.claude-rd"
    return Path(os.path.expanduser(raw))


def _codex_home(env: Mapping[str, str], override: Path | None) -> Path:
    if override is not None:
        return override
    raw = (env.get("CODEX_HOME") or "").strip() or "~/.codex"
    return Path(os.path.expanduser(raw))


def _agy_home(env: Mapping[str, str]) -> Path:
    raw = (env.get("ANTIGRAVITY_HOME") or env.get("GEMINI_HOME") or "").strip() or "~/.gemini"
    return Path(os.path.expanduser(raw))


def probe_claude(env: Mapping[str, str], config_dir: Path) -> ProviderCaps:
    evidence: list[str] = []
    for name in CLAUDE_PUSH_ENV:
        if _env_flag(env, name):
            evidence.append(f"env:{name} is set")
    settings = _read_json(config_dir / "settings.json")
    channels = settings.get("channels")
    if isinstance(channels, dict) and channels.get("enabled"):
        evidence.append("config:settings.json channels.enabled")
    if evidence:
        return ProviderCaps("push", tuple(evidence))
    if config_dir.is_dir():
        evidence.append("no CLAUDE_CODE_SSE_PORT and channels disabled in settings.json")
    else:
        evidence.append(f"missing config dir {config_dir}")
    return ProviderCaps("poll", tuple(evidence))


def probe_codex(codex_home: Path) -> ProviderCaps:
    doc = _read_json(codex_home / "hooks.json")
    hooked = _hook_commands(doc, *CODEX_HOOK_EVENTS)
    if hooked:
        return ProviderCaps("hook", tuple(f"hooks.json:{name} configured" for name in hooked))
    return ProviderCaps(
        "poll",
        ("no UserPromptSubmit/Stop hooks in hooks.json; Codex has no native session-to-session messaging"),
    )


def probe_agy(agy_home: Path) -> ProviderCaps:
    evidence: list[str] = []
    for rel, events in (
        (agy_home / ".agents" / "hooks.json", AGY_HOOK_EVENTS),
        (agy_home / "settings.json", ("AfterAgent", "AfterTool")),
        (agy_home / ".gemini" / "settings.json", ("AfterAgent", "AfterTool")),
    ):
        hooked = _hook_commands(_read_json(rel), *events)
        for name in hooked:
            evidence.append(f"{rel.name}:{name}")
    if evidence:
        return ProviderCaps("hook", tuple(evidence))
    return ProviderCaps(
        "poll",
        ("no hook events configured; agy has no native session-to-session messaging",),
    )


def probe_providers(
    *,
    env: Mapping[str, str] | None = None,
    claude_config_dir: Path | None = None,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    """Probe all providers. Post: JSON-serializable, no secret values in strings."""
    mapping = env if env is not None else os.environ
    claude_dir = _claude_config_dir(mapping, claude_config_dir)
    codex_dir = _codex_home(mapping, codex_home)
    agy_dir = _agy_home(mapping)
    providers = {
        "claude_code": probe_claude(mapping, claude_dir),
        "codex_cli": probe_codex(codex_dir),
        "agy": probe_agy(agy_dir),
    }
    body = {
        "as_of": datetime.now(UTC).date().isoformat(),
        "providers": {
            name: {"delivery": caps.delivery, "evidence": list(caps.evidence)} for name, caps in providers.items()
        },
    }
    sanitized = sanitize_text(json.dumps(body, ensure_ascii=False))
    parsed = json.loads(sanitized)
    if not isinstance(parsed, dict):
        raise ProbeContractError("sanitized probe must be an object")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(probe_providers(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
