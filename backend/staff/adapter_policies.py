"""Permission policies, tool allow-lists, and read-only flags for provider adapters (#1484, #1586, #1595).

Extracted from staff.adapters to keep file lengths within the 500-line budget.
Contains the shared shell allow/deny lists, permission bypass constants, and policy generators.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# ── Read-only chat turns (#1484) ────────────────────────────────────────────
# Provider-neutral read-only tool vocabulary for ``chat.read_only_tools`` in role
# files, mapped to Claude Code tool names. One table serves the adapter and the
# role validator (DRY). Anything not listed here is not read-only by definition.
CHAT_READ_ONLY_TOOLS: dict[str, tuple[str, ...]] = {
    "view_file": ("Read",),
    "search_code": ("Grep", "Glob"),
    "list_files": ("Glob",),
    "web_fetch": ("WebFetch",),
    "web_search": ("WebSearch",),
}
# Always denied on Claude chat turns, whatever the host's settings allow.
CLAUDE_WRITE_TOOLS: tuple[str, ...] = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# The explicit read-only flag each chat-capable provider gets on every turn.
_CHAT_READ_ONLY_FLAGS: dict[str, tuple[str, ...]] = {
    "claude": ("--permission-mode", "default"),
    "claude-ollama": ("--permission-mode", "default"),
    "codex": ("--sandbox", "read-only"),
    "ollama": ("--sandbox", "read-only"),
    "antigravity": ("--mode", "plan"),
    "gemini": ("--approval-mode", "plan"),
    "cursor-agent": ("--mode", "ask"),
}
# Flags and flag values that switch a CLI's permission checks off. No staff argv,
# unattended or chat, may contain one (#1586).
PERMISSION_BYPASS_FLAGS = frozenset(
    {
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "bypassPermissions",
        "danger-full-access",
        "--force",
        "--yolo",
        "yolo",
    }
)
# Chat turns are read-only, so they also never trust a workspace or auto-confirm.
_CHAT_BYPASS_FLAGS = PERMISSION_BYPASS_FLAGS | {"--trust", "-y"}


# ── Unattended runs (#1586) ─────────────────────────────────────────────────
# Shell command prefixes an unattended run may execute without a prompt. One
# table, rendered per provider (Claude tool rules, Gemini policy). Playbooks use
# curl for the local dashboard API and `git push --force-with-lease` on their
# own branches, so neither is denied here.
UNATTENDED_SHELL_ALLOW: tuple[str, ...] = (
    "git",
    "gh",
    "python",
    "python3",
    "pytest",
    "ruff",
    "black",
    "mypy",
    "pre-commit",
    "uv",
    "pip",
    "npm",
    "npx",
    "node",
    "tsc",
    "make",
    "curl",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "wc",
    "sort",
    "diff",
    "mkdir",
    "cp",
    "mv",
    "echo",
    "sed",
    "jq",
)
# Always refused, whatever the allow-list says.
UNATTENDED_SHELL_DENY: tuple[str, ...] = (
    "sudo",
    "su",
    "gh repo delete",
    "gh secret",
    "gh auth",
    "gh pr merge --admin",
    "systemctl",
    "shutdown",
    "reboot",
)
# Claude tools an unattended run may use besides the allow-listed shell commands.
_CLAUDE_UNATTENDED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "TodoWrite",
    "Task",
    "WebFetch",
    "WebSearch",
)


class ChatReadOnlyUnsupportedError(RuntimeError):
    """The provider has no known read-only mode, so a chat turn must not run on it."""


class UnattendedUnsupportedError(RuntimeError):
    """The provider cannot run unattended without bypassing its permissions (#1586)."""


def claude_unattended_tools() -> tuple[str, str]:
    """``(--allowedTools, --disallowedTools)`` values for an unattended Claude run.

    Post: every shell rule is a ``Bash(<prefix>:*)`` rule; the bare ``Bash`` tool
    is never allowed, so an unlisted command is refused, not prompted.
    """
    allowed = [*_CLAUDE_UNATTENDED_TOOLS, *(f"Bash({cmd}:*)" for cmd in UNATTENDED_SHELL_ALLOW)]
    denied = [f"Bash({cmd}:*)" for cmd in UNATTENDED_SHELL_DENY]
    return ",".join(allowed), ",".join(denied)


# ── Code-read-only unattended runs (#1659) ──────────────────────────────────
READ_ONLY_RUN_SHELL_ALLOW: tuple[str, ...] = (
    "gh pr view",
    "gh pr list",
    "gh pr checks",
    "gh pr diff",
    "gh issue view",
    "gh issue list",
    "gh run view",
    "gh run list",
    "gh search",
    "git log",
    "git show",
    "git diff",
    "git status",
    "git branch --list",
    "git rev-parse",
    "git ls-files",
    "curl -s http://127.0.0.1:8321/api/",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "wc",
    "sort",
    "jq",
)
READ_ONLY_RUN_SHELL_DENY: tuple[str, ...] = UNATTENDED_SHELL_DENY + (
    "git push",
    "git commit",
    "gh pr create",
    "gh pr merge",
    "gh pr edit",
    "gh issue create",
    "gh issue edit",
    "gh issue close",
    "gh issue comment",
    "gh pr comment",
    "gh release",
    "gh api",
)
_CLAUDE_READ_ONLY_RUN_TOOLS = ("Read", "Grep", "Glob", "TodoWrite", "WebFetch", "WebSearch")
READ_ONLY_RUN_PROVIDERS: frozenset[str] = frozenset({"claude", "antigravity"})


def antigravity_permissions(
    read_only: bool = False,
    *,
    workdir: Path | None = None,
    gitdir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Render agy shell command permissions and optionally merge them into settings.

    Existing settings and permission entries are retained. When ``workdir`` is
    provided, both the configured agy home and worktree settings are prepared;
    ``gitdir`` additionally excludes the generated ``.gemini`` directory.
    """
    allow_commands = READ_ONLY_RUN_SHELL_ALLOW if read_only else UNATTENDED_SHELL_ALLOW
    deny_commands = READ_ONLY_RUN_SHELL_DENY if read_only else UNATTENDED_SHELL_DENY
    allow = list(
        dict.fromkeys(
            rule
            for prefix in allow_commands
            for rule in (f"command({prefix}*)", f"command({prefix})", f"command({prefix} *)")
        )
    )
    deny = list(
        dict.fromkeys(
            rule
            for prefix in deny_commands
            for rule in (f"command({prefix}*)", f"command({prefix})", f"command({prefix} *)")
        )
    )

    if workdir is not None:
        from staff.cli_oauth_session import resolve_antigravity_home

        agy_home = resolve_antigravity_home()
        for settings_path in (
            agy_home / "settings.json",
            agy_home / "antigravity-cli" / "settings.json",
            workdir / ".gemini" / "settings.json",
        ):
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                settings = {}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot read Antigravity settings {settings_path}: {exc}") from exc
            if not isinstance(settings, dict):
                raise ValueError(f"Antigravity settings must be a JSON object: {settings_path}")
            permissions = settings.setdefault("permissions", {})
            if not isinstance(permissions, dict):
                raise ValueError(f"Antigravity permissions must be a JSON object: {settings_path}")
            for key, rules in (("allow", allow), ("deny", deny)):
                current = permissions.setdefault(key, [])
                if not isinstance(current, list) or not all(isinstance(item, str) for item in current):
                    raise ValueError(f"Antigravity permissions.{key} must be a string list: {settings_path}")
                permissions[key] = list(dict.fromkeys([*current, *rules]))
            trusted = settings.setdefault("trustedWorkspaces", [])
            if isinstance(trusted, list):
                for p_str in (str(workdir), str(workdir.parent)):
                    if p_str not in trusted:
                        trusted.append(p_str)
            settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

        if gitdir is not None:
            exclude = gitdir / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if ".gemini/" not in existing.splitlines():
                separator = "" if not existing or existing.endswith("\n") else "\n"
                exclude.write_text(existing + separator + ".gemini/\n", encoding="utf-8")
    return allow, deny


def claude_read_only_run_tools() -> tuple[str, str]:
    """Tool allowlist and denylist for code-read-only unattended Claude runs (#1659).

    Uses an explicit allowlist for shell commands and tools. Note that the curl
    rule is limited to the local dashboard, whose own auth and the run token's scopes
    are the backstop for non-GET methods.

    Post: no write tool in CLAUDE_WRITE_TOOLS is allowed; no bare git/gh/curl rule is allowed.
    """
    allowed = [*_CLAUDE_READ_ONLY_RUN_TOOLS, *(f"Bash({cmd}:*)" for cmd in READ_ONLY_RUN_SHELL_ALLOW)]
    denied = [*CLAUDE_WRITE_TOOLS, *(f"Bash({cmd}:*)" for cmd in READ_ONLY_RUN_SHELL_DENY)]
    assert not any(tool in allowed for tool in CLAUDE_WRITE_TOOLS), "no write tool allowed"  # noqa: S101
    assert not any(rule in allowed for rule in ("Bash(git:*)", "Bash(gh:*)", "Bash(curl:*)")), (  # noqa: S101
        "no bare git/gh/curl rule allowed"
    )
    return ",".join(allowed), ",".join(denied)


def _toml_list(items: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(item) for item in items) + "]"


def gemini_policy_toml() -> str:
    """Gemini CLI policy for unattended runs: allow the shared list, deny above it.

    Headless Gemini treats anything that would ask as a deny, so commands on
    neither list are refused.
    """
    return (
        "# Generated by Runner_Dashboard backend/staff/adapters.py (#1586). Do not edit.\n"
        "[[rule]]\n"
        'toolName = "run_shell_command"\n'
        f"commandPrefix = {_toml_list(UNATTENDED_SHELL_ALLOW)}\n"
        'decision = "allow"\n'
        "priority = 100\n\n"
        "[[rule]]\n"
        'toolName = "run_shell_command"\n'
        f"commandPrefix = {_toml_list(UNATTENDED_SHELL_DENY)}\n"
        'decision = "deny"\n'
        "priority = 200\n"
    )


def claude_allowed_tools(read_only_tools: Sequence[str]) -> list[str]:
    """Map provider-neutral read-only tool names to Claude tool names, in order.

    Pre: every name is a key of :data:`CHAT_READ_ONLY_TOOLS` (``ValueError`` otherwise).
    Post: no duplicates; never contains a :data:`CLAUDE_WRITE_TOOLS` entry.
    """
    unknown = [name for name in read_only_tools if name not in CHAT_READ_ONLY_TOOLS]
    if unknown:
        raise ValueError(f"not read-only chat tools: {unknown}; known: {sorted(CHAT_READ_ONLY_TOOLS)}")
    out: list[str] = []
    for name in read_only_tools:
        out.extend(tool for tool in CHAT_READ_ONLY_TOOLS[name] if tool not in out)
    return out


_CAMEL_USAGE = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadTokens": "cache_read_input_tokens",
    "cacheWriteTokens": "cache_creation_input_tokens",
}


def _extract_text(raw: dict[str, Any]) -> str:
    """Best-effort human text from a stream-json event (claude/agy/codex shapes)."""
    if isinstance(raw.get("item"), dict):  # codex --json: only agent messages are answers
        item = raw["item"]
        return str(item.get("text") or "") if item.get("type") == "agent_message" else ""
    for key in (
        "result",
        "text",
        "message",
        "content",
        "output",
        "response",
        "delta",
    ):  # agy: response, claude: delta
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
    nested = raw.get("result")
    if not isinstance(src, dict) and isinstance(nested, dict):
        src = nested.get("usage")  # agy reports usage inside the result event
    if isinstance(src, dict):
        token_keys = (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "total_tokens",
        )
        for key in token_keys:
            if isinstance(src.get(key), int | float):
                usage[key] = src[key]
        for camel, snake in _CAMEL_USAGE.items():  # cursor-agent reports camelCase
            if snake not in usage and isinstance(src.get(camel), int | float):
                usage[snake] = src[camel]
    for key in ("total_cost_usd", "cost_usd"):
        if isinstance(raw.get(key), int | float):
            usage["cost_usd"] = float(raw[key])
    return usage
