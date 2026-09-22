"""Provider adapters: how each coding CLI is launched and how its output is read.

One adapter per CLI. An adapter is pure data + two pure functions:

* ``build_command(prompt, workdir, model)`` → argv list (never a shell string).
* ``parse_line(line)`` → a flat event dict ``{"kind", "text", "usage"?}``.

Adapters never spawn anything themselves; ``runner.py`` owns the subprocess.
Flags below were verified against the installed CLIs on 2026-09-22:

  claude  -p --output-format stream-json --verbose
  codex   exec --full-auto
  agy     --print --output-format stream-json --dangerously-skip-permissions
  gemini  -p
  cursor-agent -p
  ollama  run <model>
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from typing import Any

# Provider ids used by staff roles. They intentionally match the
# ``dashboard_id`` values in agent_remediation/provider_registry.py where an
# entry exists there (claude_code_cli → "claude", codex_cli → "codex").
ProviderId = str


@dataclass(frozen=True)
class ProviderAdapter:
    """Launch recipe for one CLI provider."""

    provider_id: ProviderId
    label: str
    executable: str
    # argv template; ``{prompt}``, ``{model}`` and ``{workdir}`` are substituted.
    argv: tuple[str, ...]
    default_model: str | None = None
    # When True the prompt is written to stdin instead of an argv slot.
    prompt_via_stdin: bool = False
    json_lines: bool = False
    max_concurrency: int = 1
    notes: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)

    def build_command(self, prompt: str, workdir: str, model: str | None = None) -> list[str]:
        """Return argv for one run.

        Pre: ``prompt`` is non-empty; ``workdir`` is an existing directory path.
        Post: the returned list never contains an unexpanded ``{...}`` slot.
        """
        assert prompt.strip(), "prompt must be non-empty"  # noqa: S101
        chosen_model = model or self.default_model or ""
        out: list[str] = [self.executable]
        for part in self.argv:
            if part == "{model}" and not chosen_model:
                # Drop the flag that precedes an empty model slot.
                if out and out[-1].startswith("-"):
                    out.pop()
                continue
            out.append(part.replace("{prompt}", prompt).replace("{model}", chosen_model).replace("{workdir}", workdir))
        assert not any("{prompt}" in p or "{model}" in p or "{workdir}" in p for p in out)  # noqa: S101
        return out

    def parse_line(self, line: str) -> dict[str, Any]:
        """Turn one stdout line into a flat event.

        JSON-lines providers yield ``{"kind": <type>, "text": <best-effort text>,
        "usage": {...}?, "raw": <dict>}``. Plain-text providers yield
        ``{"kind": "text", "text": line}``. Never raises on malformed input.
        """
        stripped = line.rstrip("\r\n")
        if not self.json_lines or not stripped.startswith("{"):
            return {"kind": "text", "text": stripped}
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            return {"kind": "text", "text": stripped}
        if not isinstance(raw, dict):
            return {"kind": "text", "text": stripped}
        kind = str(raw.get("type") or raw.get("event") or raw.get("kind") or "json")
        text = _extract_text(raw)
        event: dict[str, Any] = {"kind": kind, "text": text, "raw": raw}
        usage = _extract_usage(raw)
        if usage:
            event["usage"] = usage
        return event

    def installed(self) -> bool:
        return shutil.which(self.executable) is not None


def _extract_text(raw: dict[str, Any]) -> str:
    """Best-effort human text from a stream-json event (claude/agy/codex shapes)."""
    for key in ("result", "text", "message", "content", "output"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            inner = _extract_text(val)
            if inner:
                return inner
        if isinstance(val, list):
            parts = [p.get("text", "") for p in val if isinstance(p, dict) and p.get("type") == "text"]
            joined = "\n".join(p for p in parts if p)
            if joined.strip():
                return joined
    return ""


def _extract_usage(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull token/cost accounting out of a final event when present."""
    usage: dict[str, Any] = {}
    src = raw.get("usage")
    if isinstance(src, dict):
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "total_tokens",
        ):
            if isinstance(src.get(key), int | float):
                usage[key] = src[key]
    for key in ("total_cost_usd", "cost_usd"):
        if isinstance(raw.get(key), int | float):
            usage["cost_usd"] = float(raw[key])
    return usage


ADAPTERS: dict[ProviderId, ProviderAdapter] = {
    "claude": ProviderAdapter(
        provider_id="claude",
        label="Claude Code CLI",
        executable="claude",
        argv=(
            "-p",
            "{prompt}",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--model",
            "{model}",
        ),
        json_lines=True,
        notes="Emits usage + total_cost_usd in the final result event.",
    ),
    "codex": ProviderAdapter(
        provider_id="codex",
        label="Codex CLI",
        executable="codex",
        argv=("exec", "--full-auto", "--model", "{model}", "{prompt}"),
        notes="Plain text stdout; cost derived from wall time until --json is adopted.",
    ),
    "antigravity": ProviderAdapter(
        provider_id="antigravity",
        label="Antigravity CLI (agy)",
        executable="agy",
        argv=(
            "--print",
            "{prompt}",
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "--add-dir",
            "{workdir}",
            "--model",
            "{model}",
        ),
        json_lines=True,
    ),
    "gemini": ProviderAdapter(
        provider_id="gemini",
        label="Gemini CLI",
        executable="gemini",
        argv=("-p", "{prompt}", "--model", "{model}"),
    ),
    "cursor-agent": ProviderAdapter(
        provider_id="cursor-agent",
        label="Cursor Agent CLI",
        executable="cursor-agent",
        argv=("-p", "{prompt}", "--model", "{model}"),
        notes="Grok models are available here through the Cursor subscription.",
    ),
    "ollama": ProviderAdapter(
        provider_id="ollama",
        label="Ollama (local)",
        executable="ollama",
        argv=("run", "{model}"),
        default_model="llama3.1",
        prompt_via_stdin=True,
        max_concurrency=2,
        notes="Read-only analysis roles only; cannot edit repositories.",
    ),
}


def get_adapter(provider_id: str) -> ProviderAdapter:
    """Return the adapter for ``provider_id`` or raise ``KeyError``."""
    return ADAPTERS[provider_id]


def available_providers() -> dict[str, bool]:
    """Map provider id → whether its executable is on PATH on this node."""
    return {pid: adapter.installed() for pid, adapter in ADAPTERS.items()}
