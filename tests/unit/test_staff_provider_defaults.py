"""Repo-managed staff provider default models (issue #2022)."""

from __future__ import annotations

from staff import adapters as adapters_mod
from staff.provider_defaults import default_model_for_provider


def test_codex_default_model_from_repo_config() -> None:
    assert default_model_for_provider("codex") == "gpt-6-luna"


def test_antigravity_default_model_from_repo_config() -> None:
    assert default_model_for_provider("antigravity") == "gemini-3.8-flash-high"


def test_codex_build_command_passes_default_model_explicitly() -> None:
    codex = adapters_mod.ADAPTERS["codex"]
    argv = codex.build_command("do work", "/tmp/wt", model=None)
    model_idx = argv.index("--model")
    assert argv[model_idx + 1] == "gpt-6-luna"


def test_run_body_model_overrides_codex_default() -> None:
    codex = adapters_mod.ADAPTERS["codex"]
    argv = codex.build_command("do work", "/tmp/wt", model="gpt-custom")
    assert argv[argv.index("--model") + 1] == "gpt-custom"
