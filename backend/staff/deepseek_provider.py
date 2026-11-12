"""Explicitly enabled DeepSeek API route through Claude Code."""

import os
import shutil
from collections.abc import Mapping


class DeepSeekNotEnabled(ValueError):
    """The paid route has not been configured explicitly."""


def deepseek_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    key = source.get("DEEPSEEK_API_KEY", "").strip()
    if source.get("STAFF_DEEPSEEK_API_ENABLED", "").lower() != "true" or not key:
        raise DeepSeekNotEnabled("DeepSeek API requires explicit enablement and its own credential")
    return {
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_API_KEY": "",
    }


def deepseek_available() -> bool:
    try:
        deepseek_environment()
    except DeepSeekNotEnabled:
        return False
    return shutil.which("claude") is not None
