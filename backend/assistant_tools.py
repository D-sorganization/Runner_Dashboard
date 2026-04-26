"""
Assistant tool-use layer for the runner dashboard (Issue #89).

Implements:
- Anthropic tool-use loop for the /api/assistant/chat endpoint when
  ``tools_enabled: true`` is set.
- Tool allowlist (read-only auto-run; state-changing require confirmation).
- POST /api/assistant/tool/execute — server-side tool execution with audit.
- GET  /api/assistant/audit-history — last 50 assistant tool executions.

Safety contract:
- Every tool is in a strict allowlist.  Unknown tool names are rejected with 422.
- State-changing tools require an explicit ``confirmation`` block in the request;
  without it the endpoint returns 403.
- Every confirmed execution is appended to ``_TOOL_AUDIT_LOG`` (in-memory, last
  500 entries) and can be queried via /api/assistant/audit-history.

See ``TOOL_ALLOWLIST`` for the full set of permitted tools.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

log = logging.getLogger("dashboard.assistant_tools")

# ─── Allowlist ────────────────────────────────────────────────────────────────

TOOL_ALLOWLIST: dict[str, dict[str, Any]] = {
    # ── Read-only (no confirmation required) ──────────────────────────────────
    "list_open_prs": {
        "description": "List all open PRs across the fleet.",
        "requires_confirmation": False,
        "backend_endpoint": "GET /api/prs",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of PRs to return (default 50).",
                    "default": 50,
                }
            },
        },
    },
    "list_open_issues": {
        "description": "List open issues across the fleet.",
        "requires_confirmation": False,
        "backend_endpoint": "GET /api/issues",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of issues to return.",
                    "default": 50,
                }
            },
        },
    },
    "get_failed_runs": {
        "description": "Return recent failed workflow runs.",
        "requires_confirmation": False,
        "backend_endpoint": "GET /api/runs/enriched?conclusion=failure",
        "input_schema": {"type": "object", "properties": {}},
    },
    "get_repos": {
        "description": "List repositories in the organisation.",
        "requires_confirmation": False,
        "backend_endpoint": "GET /api/repos",
        "input_schema": {"type": "object", "properties": {}},
    },
    "refresh_dashboard_data": {
        "description": "Instruct the frontend to re-fetch all dashboard data.",
        "requires_confirmation": False,
        "backend_endpoint": "client-side instruction",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ── State-changing (confirmation required) ────────────────────────────────
    "dispatch_agent_to_pr": {
        "description": "Dispatch an AI agent to address a specific pull request.",
        "requires_confirmation": True,
        "backend_endpoint": "POST /api/prs/dispatch",
        "input_schema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "Owner/repo slug."},
                "number": {"type": "integer", "description": "PR number."},
                "provider": {
                    "type": "string",
                    "description": "Agent provider (e.g. claude_code_cli).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Instruction for the agent.",
                },
            },
            "required": ["repository", "number", "provider", "prompt"],
        },
    },
    "dispatch_agent_to_issue": {
        "description": "Dispatch an AI agent to address a specific issue.",
        "requires_confirmation": True,
        "backend_endpoint": "POST /api/issues/dispatch",
        "input_schema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "number": {"type": "integer"},
                "provider": {"type": "string"},
                "prompt": {"type": "string"},
            },
            "required": ["repository", "number", "provider", "prompt"],
        },
    },
    "quick_dispatch_agent": {
        "description": "Quick-dispatch an AI agent with a freeform prompt.",
        "requires_confirmation": True,
        "backend_endpoint": "POST /api/agents/quick-dispatch",
        "input_schema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "provider": {"type": "string"},
                "prompt": {"type": "string"},
            },
            "required": ["repository", "provider", "prompt"],
        },
    },
    "dispatch_remediation": {
        "description": "Dispatch the remediation workflow for a repository.",
        "requires_confirmation": True,
        "backend_endpoint": "POST /api/agent-remediation/dispatch",
        "input_schema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "provider": {"type": "string"},
            },
            "required": ["repository"],
        },
    },
}


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return Anthropic-compatible tool definitions for the allowlist."""
    tools = []
    for name, spec in TOOL_ALLOWLIST.items():
        tools.append(
            {
                "name": name,
                "description": spec["description"],
                "input_schema": spec["input_schema"],
            }
        )
    return tools


# ─── Audit log ────────────────────────────────────────────────────────────────

_MAX_AUDIT_ENTRIES = 500
_TOOL_AUDIT_LOG: deque[dict[str, Any]] = deque(maxlen=_MAX_AUDIT_ENTRIES)


def _record_audit(
    *,
    tool_name: str,
    tool_call_id: str,
    inputs: dict[str, Any],
    outcome: str,
    success: bool,
    approved_by: str,
    note: str,
) -> dict[str, Any]:
    """Append an audit entry and return it."""
    entry: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "inputs": inputs,
        "outcome": outcome,
        "success": success,
        "assistant": True,
        "approved_by": approved_by,
        "note": note,
    }
    _TOOL_AUDIT_LOG.append(entry)
    log.info(
        "tool audit: tool=%s call_id=%s success=%s",
        tool_name,
        tool_call_id,
        success,
    )
    return entry


def get_audit_history(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent *limit* audit entries, newest-first."""
    entries = list(_TOOL_AUDIT_LOG)
    entries.reverse()
    return entries[:limit]


# ─── Anthropic chat-with-tools ────────────────────────────────────────────────


async def call_anthropic_with_tools(
    *,
    api_key: str,
    prompt: str,
    context: dict[str, Any],
    model: str = "claude-haiku-4-5-20251001",
    tools_enabled: bool = True,
) -> dict[str, Any]:
    """
    Send a chat message to Anthropic and return a dict with:
      - ``message``      : dict with role/content
      - ``stop_reason``  : "end_turn" | "tool_use"
      - ``tool_calls``   : list[dict] — empty when stop_reason != "tool_use"

    Each tool_call dict has:
      ``id``, ``name``, ``input`` (dict), ``requires_confirmation`` (bool)
    """
    import httpx  # already a dep of server.py

    system_prompt = (
        "You are an AI assistant embedded in the D-sorganization runner dashboard. "
        "You help operators manage GitHub Actions runners and fix CI/CD issues. "
        "You have access to dashboard tools to fetch live data and dispatch agents. "
        "Always be concise. When you need to call a tool, call exactly one tool at a time "
        "unless the request clearly requires simultaneous calls. "
        "For state-changing tools, explain what you are about to do before calling the tool."
    )

    context_text = (
        f"Current tab: {context.get('current_tab', 'unknown')}. "
        f"Selected run: {context.get('selected_run_id')}. "
    )
    if context.get("dashboard_state"):
        context_text += (
            f"Dashboard state summary: {str(context['dashboard_state'])[:400]}"
        )

    messages = [
        {
            "role": "user",
            "content": f"[Dashboard context]\n{context_text}\n\n[User message]\n{prompt}",
        }
    ]

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": messages,
    }
    if tools_enabled:
        payload["tools"] = get_tool_definitions()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )

    if resp.status_code != 200:
        log.error("Anthropic API error %s: %s", resp.status_code, resp.text[:200])
        raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    stop_reason = data.get("stop_reason", "end_turn")
    content_blocks = data.get("content", [])

    # Extract plain text
    text_blocks = [b["text"] for b in content_blocks if b.get("type") == "text"]
    message_text = "\n".join(text_blocks)

    # Extract tool calls
    tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
    tool_calls = []
    for block in tool_use_blocks:
        name = block.get("name", "")
        spec = TOOL_ALLOWLIST.get(name, {})
        tool_calls.append(
            {
                "id": block.get("id", ""),
                "name": name,
                "input": block.get("input", {}),
                "requires_confirmation": spec.get("requires_confirmation", True),
            }
        )

    return {
        "message": {"role": "assistant", "content": message_text},
        "stop_reason": stop_reason,
        "tool_calls": tool_calls,
    }


# ─── Tool execution ───────────────────────────────────────────────────────────


async def execute_tool(
    *,
    tool_name: str,
    tool_call_id: str,
    inputs: dict[str, Any],
    confirmation: dict[str, Any] | None,
    # HTTP client helpers injected from server.py to avoid circular imports
    gh_api_fn: Any,
    dispatch_fn: Any,
) -> dict[str, Any]:
    """
    Execute a named tool from the allowlist.

    Returns a dict with:
      - ``result``      : any JSON-serialisable result
      - ``audit_entry`` : the recorded audit log entry

    Raises:
      - ``PermissionError`` if state-changing tool called without confirmation
      - ``KeyError``        if tool_name is not in the allowlist
    """
    if tool_name not in TOOL_ALLOWLIST:
        raise KeyError(f"Tool '{tool_name}' is not in the allowlist")

    spec = TOOL_ALLOWLIST[tool_name]
    requires_conf = spec["requires_confirmation"]

    approved_by = "n/a"
    note = ""

    if requires_conf:
        if not confirmation:
            raise PermissionError(
                f"Tool '{tool_name}' requires explicit user confirmation"
            )
        approved_by = confirmation.get("approved_by", "operator")
        note = confirmation.get("note", "")

    # ── Execute ───────────────────────────────────────────────────────────────
    result: Any = None
    success = True
    outcome = "ok"

    try:
        if tool_name == "list_open_prs":
            result = await gh_api_fn("/api/prs")
        elif tool_name == "list_open_issues":
            result = await gh_api_fn("/api/issues")
        elif tool_name == "get_failed_runs":
            result = await gh_api_fn("/api/runs/enriched?conclusion=failure")
        elif tool_name == "get_repos":
            result = await gh_api_fn("/api/repos")
        elif tool_name == "refresh_dashboard_data":
            result = {"action": "refresh", "status": "client_instruction_sent"}
        elif tool_name in (
            "dispatch_agent_to_pr",
            "dispatch_agent_to_issue",
            "quick_dispatch_agent",
            "dispatch_remediation",
        ):
            result = await dispatch_fn(tool_name, inputs)
        else:
            raise KeyError(f"No executor for tool '{tool_name}'")

    except Exception as exc:
        success = False
        outcome = f"error: {exc}"
        result = {"error": str(exc)}
        log.error("tool execute error tool=%s: %s", tool_name, exc)

    audit_entry = _record_audit(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        inputs=inputs,
        outcome=outcome,
        success=success,
        approved_by=approved_by,
        note=note,
    )

    return {"result": result, "audit_entry": audit_entry}
