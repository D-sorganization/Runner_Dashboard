"""Where the Ollama server is and how agent CLIs reach it (issue #1252).

Ollama models run inside a real agent harness (Codex ``--oss`` or Claude Code
against Ollama's Anthropic-compatible API), never as bare ``ollama run``
chat, so they can edit, commit and open PRs like the other providers.

On the fleet nodes the Ollama server is usually the Windows app, which a
NAT-mode WSL distro reaches through its default gateway, not ``localhost``.
Resolution order: ``STAFF_OLLAMA_URL``; ``127.0.0.1:11434`` when something
listens there; the WSL default gateway on port 11434; ``127.0.0.1`` as the
last resort (the run then fails loudly with a connection error).
"""

from __future__ import annotations

import os
import socket
import struct
from pathlib import Path

OLLAMA_PORT = 11434
DEFAULT_OLLAMA_MODEL = "glm-5.3-flash:cloud"
_ROUTE_TABLE = Path("/proc/net/route")


def _listening(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def default_gateway(route_table: Path = _ROUTE_TABLE) -> str | None:
    """IPv4 default gateway from ``/proc/net/route``, or ``None`` when absent."""
    try:
        lines = route_table.read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000":
            return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    return None


def ollama_base_url() -> str:
    """Base URL of the Ollama server, without a trailing slash or ``/v1``."""
    configured = os.environ.get("STAFF_OLLAMA_URL", "").strip().rstrip("/")
    if configured:
        return configured
    if _listening("127.0.0.1", OLLAMA_PORT):
        return f"http://127.0.0.1:{OLLAMA_PORT}"
    gateway = default_gateway()
    if gateway and _listening(gateway, OLLAMA_PORT):
        return f"http://{gateway}:{OLLAMA_PORT}"
    return f"http://127.0.0.1:{OLLAMA_PORT}"


def codex_ollama_env() -> dict[str, str]:
    """Env for ``codex exec --oss --local-provider ollama``."""
    base = ollama_base_url()
    return {"CODEX_OSS_BASE_URL": f"{base}/v1", "OLLAMA_HOST": base}


def claude_ollama_env() -> dict[str, str]:
    """Env for Claude Code on Ollama's Anthropic-compatible ``/v1/messages``.

    Uses its own ``CLAUDE_CONFIG_DIR`` so the Claude seat's OAuth credentials
    are never read, refreshed or overwritten by an Ollama-backed run.
    """
    config_dir = Path.home() / ".config" / "runner-dashboard" / "claude-ollama"
    config_dir.mkdir(parents=True, exist_ok=True)
    return {
        "ANTHROPIC_BASE_URL": ollama_base_url(),
        "ANTHROPIC_AUTH_TOKEN": "ollama",
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CONFIG_DIR": str(config_dir),
    }
