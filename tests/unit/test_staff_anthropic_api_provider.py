"""anthropic-api provider adapter: secret loading, redaction and inert status (issue #3123 slice 2).

Ships inert: no key exists yet, so these tests only exercise secret
resolution (env + vault fallback) and the pure status projection against a
fake key value — no network call, no live registry/dispatch wiring (slice 4).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from staff import anthropic_api_provider
from staff.anthropic_api_budget import AnthropicApiBudgetGate
from staff.anthropic_api_provider import (
    DEFAULT_KEY_ENV_VAR,
    AnthropicApiProviderState,
    key_env_var,
    load_api_key,
    provider_state,
)
from staff.redaction import redact_sensitive_content
from staff.store import RunRecord, RunStore

pytestmark = pytest.mark.unit

# Synthetic value shaped like a real Anthropic key so redaction tests exercise
# the real pattern; never a real credential.  # pragma: allowlist secret
_FAKE_KEY = "sk-ant-api03-fake0123456789fake0123456789fake0123456789fake"  # pragma: allowlist secret


# ── key_env_var ───────────────────────────────────────────────────────────


def test_key_env_var_defaults() -> None:
    assert key_env_var(env={}) == DEFAULT_KEY_ENV_VAR


def test_key_env_var_override() -> None:
    assert (
        key_env_var(env={"ANTHROPIC_API_KEY_ENV_VAR": "CUSTOM_ANTHROPIC_KEY"})  # pragma: allowlist secret
        == "CUSTOM_ANTHROPIC_KEY"
    )


def test_key_env_var_blank_override_falls_back() -> None:
    assert key_env_var(env={"ANTHROPIC_API_KEY_ENV_VAR": "   "}) == DEFAULT_KEY_ENV_VAR  # pragma: allowlist secret


# ── load_api_key ──────────────────────────────────────────────────────────


def test_load_api_key_reads_env_first() -> None:
    assert load_api_key(env={DEFAULT_KEY_ENV_VAR: _FAKE_KEY}) == _FAKE_KEY


def test_load_api_key_honors_configurable_var_name() -> None:
    env = {
        "ANTHROPIC_API_KEY_ENV_VAR": "CUSTOM_ANTHROPIC_KEY",  # pragma: allowlist secret
        "CUSTOM_ANTHROPIC_KEY": _FAKE_KEY,
    }
    assert load_api_key(env=env) == _FAKE_KEY


def test_load_api_key_blank_env_value_falls_through_to_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_api_provider, "resolve_vault_handle", lambda handle, *, actor: _FAKE_KEY)
    assert load_api_key(env={DEFAULT_KEY_ENV_VAR: "   "}) == _FAKE_KEY


def test_load_api_key_falls_back_to_vault_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def _fake_resolve(handle: str, *, actor: str) -> str:
        seen["handle"] = handle
        seen["actor"] = actor
        return _FAKE_KEY

    monkeypatch.setattr(anthropic_api_provider, "resolve_vault_handle", _fake_resolve)
    assert load_api_key(env={}) == _FAKE_KEY
    assert seen["handle"] == f"$vault:{DEFAULT_KEY_ENV_VAR}"


def test_load_api_key_none_when_neither_env_nor_vault_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(handle: str, *, actor: str) -> str:
        raise KeyError(handle)

    monkeypatch.setattr(anthropic_api_provider, "resolve_vault_handle", _raise)
    assert load_api_key(env={}) is None


def test_load_api_key_none_when_vault_scope_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(handle: str, *, actor: str) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(anthropic_api_provider, "resolve_vault_handle", _raise)
    assert load_api_key(env={}) is None


def test_load_api_key_strips_whitespace_only_vault_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_api_provider, "resolve_vault_handle", lambda handle, *, actor: "   ")
    assert load_api_key(env={}) is None


# ── provider_state ────────────────────────────────────────────────────────


def test_provider_state_not_configured_without_key() -> None:
    state = provider_state(api_key=None)
    assert state.status == "not_configured"
    assert DEFAULT_KEY_ENV_VAR in state.detail


def test_provider_state_not_configured_on_empty_string_key() -> None:
    assert provider_state(api_key="").status == "not_configured"


def test_provider_state_ready_with_key_and_no_gate() -> None:
    state = provider_state(api_key=_FAKE_KEY)
    assert state.status == "ready"


def test_provider_state_ready_when_gate_allows(tmp_path: Path) -> None:
    store = RunStore(path=tmp_path / "runs.sqlite3")
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    state = provider_state(api_key=_FAKE_KEY, budget_gate=gate, estimated_cost_usd=1.0)
    assert state.status == "ready"


def test_provider_state_budget_exhausted_when_gate_refuses(tmp_path: Path) -> None:
    store = RunStore(path=tmp_path / "runs.sqlite3")
    store.create_run(
        RunRecord(
            id="run-1",
            role="r",
            provider="anthropic-api",
            model="claude-sonnet-5",
            machine="m",
            repo="repo",
            target_kind="prompt",
            target_ref="",
            prompt="",
            status="succeeded",
            created_at="2026-10-01T10:00:00Z",
            cost_usd=199.0,
        )
    )
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    state = provider_state(api_key=_FAKE_KEY, budget_gate=gate, estimated_cost_usd=5.0)
    assert state.status == "budget_exhausted"
    assert "budget_exhausted" in state.detail


def test_provider_state_rejects_negative_estimate() -> None:
    with pytest.raises(AssertionError):
        provider_state(api_key=_FAKE_KEY, estimated_cost_usd=-1.0)


# ── redaction: the key never appears in any observable output ─────────────


def test_provider_state_detail_never_contains_the_key(tmp_path: Path) -> None:
    store = RunStore(path=tmp_path / "runs.sqlite3")
    gate = AnthropicApiBudgetGate(store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")))
    for state in (
        provider_state(api_key=None),
        provider_state(api_key=_FAKE_KEY),
        provider_state(api_key=_FAKE_KEY, budget_gate=gate, estimated_cost_usd=0.0),
    ):
        assert _FAKE_KEY not in state.detail


def test_load_api_key_never_logs_the_key(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_api_provider, "resolve_vault_handle", lambda handle, *, actor: _FAKE_KEY)
    with caplog.at_level(logging.DEBUG):
        load_api_key(env={DEFAULT_KEY_ENV_VAR: _FAKE_KEY})
        load_api_key(env={})
    assert _FAKE_KEY not in caplog.text


def test_shared_redactor_also_catches_the_key_shape_as_belt_and_suspenders() -> None:
    # Belt-and-suspenders: even if a key ever reached a persisted transcript
    # through some other path, the fleet-wide redactor independently masks it.
    assert redact_sensitive_content(f"key={_FAKE_KEY}") == "key=[REDACTED_API_KEY]"


def test_provider_state_is_frozen_and_slotted() -> None:
    state = AnthropicApiProviderState(status="ready", detail="ok")
    with pytest.raises(AttributeError):
        state.status = "error"  # type: ignore[misc]
