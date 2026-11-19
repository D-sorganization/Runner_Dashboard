"""Opus/Sonnet models must go through the claude provider, not antigravity.

Dieter 2026-11-16 8:02 AM PT / issue #4494: Antigravity quota for Opus and Sonnet
is limited and usually exhausted. Antigravity may still carry Gemini models.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from staff.group_models import BOARD_SEATS
from staff.panel_models import PANEL_PRESETS
from staff.seat_fallback import DEFAULT_SEAT_FALLBACK, FABLE_FALLBACK

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANTIGRAVITY_PROVIDERS = frozenset({"antigravity", "agy"})
_OPUS_SONNET_TOKEN = re.compile(r"(?:^|[^a-z0-9])(opus|sonnet)(?:[^a-z0-9]|$)")


def _is_opus_or_sonnet_model(model: str | None) -> bool:
    if not model:
        return False
    return bool(_OPUS_SONNET_TOKEN.search(model.strip().lower()))


def _offenses(pairs: Iterable[tuple[str, str | None, str]]) -> list[str]:
    found: list[str] = []
    for provider, model, source in pairs:
        if provider.lower().strip() in _ANTIGRAVITY_PROVIDERS and _is_opus_or_sonnet_model(model):
            found.append(f"{source}: provider={provider!r} model={model!r}")
    return found


def test_fable_fallback_does_not_route_opus_sonnet_via_antigravity() -> None:
    assert FABLE_FALLBACK[1] == ("claude", "claude-opus-5-5")
    assert not _offenses((p, m, "FABLE_FALLBACK") for p, m in FABLE_FALLBACK)
    assert not _offenses((p, m, "DEFAULT_SEAT_FALLBACK") for p, m in DEFAULT_SEAT_FALLBACK)


def test_panel_presets_and_board_seats_do_not_route_opus_sonnet_via_antigravity() -> None:
    pairs: list[tuple[str, str | None, str]] = []
    for preset in PANEL_PRESETS:
        preset_id = preset["id"]
        for expert in preset["experts"]:
            pairs.append(
                (
                    str(expert.get("provider") or "claude"),
                    expert.get("model"),
                    f"PANEL_PRESETS[{preset_id!r}/{expert['name']}]",
                )
            )
    for seat in BOARD_SEATS:
        pairs.append((seat.provider, seat.model, f"BOARD_SEATS[{seat.name}]"))
    assert not _offenses(pairs)


def test_staff_config_and_role_chains_do_not_route_opus_sonnet_via_antigravity() -> None:
    pairs: list[tuple[str, str | None, str]] = []

    chains = json.loads((_REPO_ROOT / "config/staff_role_provider_chains.json").read_text())
    for provider, model in (chains.get("provider_models") or {}).items():
        pairs.append((provider, model, "config/staff_role_provider_chains.json provider_models"))

    failover = json.loads((_REPO_ROOT / "config/provider_failover.json").read_text())
    for i, step in enumerate(failover.get("default_chain") or []):
        pairs.append((step.get("provider", ""), step.get("model"), f"config/provider_failover.json[{i}]"))

    tiers = json.loads((_REPO_ROOT / "config/staff_model_tiers.json").read_text())
    for tier, models in (tiers.get("models_by_tier") or {}).items():
        for provider, model in models.items():
            pairs.append((provider, model, f"config/staff_model_tiers.json[{tier}]"))

    defaults = json.loads((_REPO_ROOT / "config/staff_provider_defaults.json").read_text())
    for provider, model in (defaults.get("default_models") or {}).items():
        pairs.append((provider, model, "config/staff_provider_defaults.json"))

    from scripts.rd_barb_provider import DEFAULT_MODEL, DEFAULT_PROVIDER, PERSONA_ROLE_CHAINS

    if DEFAULT_PROVIDER in _ANTIGRAVITY_PROVIDERS:
        pairs.append((DEFAULT_PROVIDER, DEFAULT_MODEL, "rd_barb_provider.DEFAULT_PROVIDER"))
    # DEFAULT_CHAIN is provider ids only; a later antigravity step with no model is Gemini-default and allowed.
    for role, steps in PERSONA_ROLE_CHAINS.items():
        for step in steps:
            pairs.append((step.provider, step.model, f"PERSONA_ROLE_CHAINS[{role}]"))

    assert not _offenses(pairs)
