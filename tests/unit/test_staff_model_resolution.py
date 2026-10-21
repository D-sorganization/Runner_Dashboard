"""Staff run model resolution (issue #2053)."""

from __future__ import annotations

from staff import adapters as adapters_mod
from staff.model_resolution import resolve_staff_run_model


def test_codex_falls_back_to_repo_default() -> None:
    codex = adapters_mod.ADAPTERS["codex"]
    assert (
        resolve_staff_run_model(requested=None, role_model=None, provider_id="codex", adapter=codex)
        == "gpt-6-luna"
    )


def test_antigravity_falls_back_to_repo_default() -> None:
    agy = adapters_mod.ADAPTERS["antigravity"]
    assert (
        resolve_staff_run_model(requested=None, role_model=None, provider_id="antigravity", adapter=agy)
        == "gemini-3.8-flash-high"
    )


def test_request_overrides_role_and_repo_default() -> None:
    codex = adapters_mod.ADAPTERS["codex"]
    assert (
        resolve_staff_run_model(
            requested="gpt-custom",
            role_model="gpt-role",
            provider_id="codex",
            adapter=codex,
        )
        == "gpt-custom"
    )


def test_role_model_overrides_repo_default() -> None:
    codex = adapters_mod.ADAPTERS["codex"]
    assert (
        resolve_staff_run_model(
            requested=None,
            role_model="gpt-role",
            provider_id="codex",
            adapter=codex,
        )
        == "gpt-role"
    )


def test_role_model_default_is_treated_as_unset() -> None:
    codex = adapters_mod.ADAPTERS["codex"]
    assert (
        resolve_staff_run_model(requested=None, role_model="default", provider_id="codex", adapter=codex)
        == "gpt-6-luna"
    )
