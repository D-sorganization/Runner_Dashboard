"""Qwen Code CLI via QwenCloud Token Plan (OpenAI-compatible API)."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping

QWEN_TOKEN_PLAN_BASE_URL = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
QWEN_DEFAULT_MODEL = "qwen3.8-flash"

_TOKEN_PLAN_KEY_ENVS = ("QWENCLOUD_TOKEN_PLAN_API_KEY", "BAILIAN_TOKEN_PLAN_API_KEY")
_TOKEN_PLAN_KEY_PREFIX = "sk-sp-"


def token_plan_api_key(env: Mapping[str, str] | None = None) -> str:
    """Return a Token Plan key from dedicated env vars or a sk-sp- OPENAI_API_KEY."""
    source = os.environ if env is None else env
    for name in _TOKEN_PLAN_KEY_ENVS:
        value = source.get(name, "").strip()
        if value:
            return value
    openai_key = source.get("OPENAI_API_KEY", "").strip()
    if openai_key.startswith(_TOKEN_PLAN_KEY_PREFIX):
        return openai_key
    return ""


def qwen_api_key_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return only the mapped ``OPENAI_API_KEY`` when a Token Plan key is configured."""
    key = token_plan_api_key(env)
    return {"OPENAI_API_KEY": key} if key else {}


def qwen_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build full child-process env for ``qwen``; raises when no Token Plan key is configured."""
    source = os.environ if env is None else env
    key = token_plan_api_key(source)
    if not key:
        raise ValueError(
            "Qwen Token Plan requires QWENCLOUD_TOKEN_PLAN_API_KEY, BAILIAN_TOKEN_PLAN_API_KEY, "
            "or OPENAI_API_KEY with the sk-sp- Token Plan prefix"
        )
    model = source.get("OPENAI_MODEL", "").strip() or QWEN_DEFAULT_MODEL
    return {
        "OPENAI_API_KEY": key,
        "OPENAI_BASE_URL": QWEN_TOKEN_PLAN_BASE_URL,
        "OPENAI_MODEL": model,
    }


def qwen_available() -> bool:
    return shutil.which("qwen") is not None and bool(token_plan_api_key())
