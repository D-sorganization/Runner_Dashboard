"""Cloud-session staff dispatch: subscription CLIs and GitHub workflow triggers (#2627).

Builds argv (or ``gh api`` workflow payloads) for ``execution_mode: cloud`` without
injecting raw API keys — cloud sessions must use provider subscription logins only.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from dashboard_config import ORG

ExecutionMode = Literal["local", "cloud"]

API_KEY_ENV_VARS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
)

_PROVIDER_ALIASES: dict[str, str] = {"cursor": "cursor-agent", "jules_api": "jules"}
_CLOUD_CLI: frozenset[str] = frozenset({"claude", "codex"})
_CLOUD_WORKFLOW: frozenset[str] = frozenset({"cursor-agent", "jules"})
_URL_RE = re.compile(r"https://[^\s\"')]+")
_CLAUDE_URL_RE = re.compile(r"https://claude\.ai/[^\s\"')]+")
_GH_RUN_RE = re.compile(r"https://github\.com/[^\s\"')]+/actions/runs/\d+")


@dataclass(frozen=True)
class CloudDispatch:
    """Resolved cloud launch: CLI argv or ``gh api`` with JSON stdin."""

    argv: list[str]
    stdin: str | None = None
    is_workflow: bool = False


def normalize_provider(provider: str) -> str:
    return _PROVIDER_ALIASES.get(provider.strip().lower(), provider.strip().lower())


def supports_cloud_execution(provider: str, *, installed: Mapping[str, bool] | None = None) -> bool:
    """Whether this host can launch ``provider`` in cloud mode."""
    pid = normalize_provider(provider)
    if pid in _CLOUD_CLI:
        if installed is not None:
            return bool(installed.get(pid))
        return shutil.which(pid) is not None
    if pid in _CLOUD_WORKFLOW:
        if installed is not None:
            return bool(installed.get("gh"))
        return shutil.which("gh") is not None
    return False


def cloud_capabilities_payload(installed: Mapping[str, bool]) -> dict[str, Any]:
    providers = {
        pid: {"cloud": supports_cloud_execution(pid, installed=installed)}
        for pid in ("claude", "codex", "cursor-agent", "jules")
    }
    return {"execution_modes": ["local", "cloud"], "providers": providers}


def scrub_api_key_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k not in API_KEY_ENV_VARS}


def _truncate(prompt: str, limit: int = 8000) -> str:
    text = prompt.strip()
    return text[:limit] if text else "Continue the assigned staff task."


def _workflow_dispatch(provider: str, prompt: str, *, repo: str, issue: int | None, pr: int | None) -> CloudDispatch:
    if not (issue or pr):
        raise ValueError(f"{provider} cloud dispatch requires an issue or pr target")
    number = issue if issue is not None else pr
    kind = "issue" if issue is not None else "pr"
    workflow = "Staff-Cloud-Dispatch.yml"
    endpoint = f"/repos/{ORG}/Repository_Management/actions/workflows/{workflow}/dispatches"
    payload = {
        "ref": "main",
        "inputs": {
            "target_repository": repo or "Runner_Dashboard",
            "execution_mode": "cloud",
            "provider": provider,
            "prompt": _truncate(prompt),
            "number": str(number),
            "target_kind": kind,
        },
    }
    return CloudDispatch(
        argv=["gh", "api", endpoint, "--method", "POST", "--input", "-"],
        stdin=json.dumps(payload),
        is_workflow=True,
    )


def build_cloud_dispatch(
    provider: str,
    prompt: str,
    *,
    model: str | None = None,
    repo: str = "",
    issue: int | None = None,
    pr: int | None = None,
) -> CloudDispatch:
    """Build cloud launch spec; raises ``ValueError`` when unsupported on any host."""
    pid = normalize_provider(provider)
    if pid not in _CLOUD_CLI | _CLOUD_WORKFLOW:
        raise ValueError(f"provider '{provider}' does not support cloud execution")
    text = _truncate(prompt)
    if pid == "claude":
        return CloudDispatch(argv=["claude", "--remote", text])
    if pid == "codex":
        argv: list[str] = ["codex", "cloud", "--json"]
        if model:
            argv.extend(["--model", model])
        argv.append(text)
        return CloudDispatch(argv=argv)
    return _workflow_dispatch(pid, prompt, repo=repo, issue=issue, pr=pr)


def parse_remote_session_url(provider: str, text: str) -> str | None:
    """Best-effort URL from CLI stdout or ``gh`` workflow output."""
    pid = normalize_provider(provider)
    if pid == "claude":
        m = _CLAUDE_URL_RE.search(text)
        return m.group(0) if m else None
    m = _GH_RUN_RE.search(text)
    if m:
        return m.group(0)
    urls = _URL_RE.findall(text)
    return urls[0] if urls else None
