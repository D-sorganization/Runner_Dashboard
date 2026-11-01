#!/usr/bin/env python3
"""rd-barb provider selection: switchable default plus subscription-CLI failover.

The one adapter layer for "which CLI runs rd-barb next" (docs/agents/rd-barb.md).
It reuses ``staff.provider_failover_config`` so the chain logic lives in one place.

  next [--current P] [--failure CLASS] [--run-id ID]
      print {"provider", "model", "switched", "previous"} as JSON. When the
      provider changed, send a "switched provider" fyi (or an alert when the
      chain is exhausted) through scripts/rd_barb_notify.py.

Config keys (env or the git-ignored rd-barb.env read by rd_barb_notify.load_config):
RD_BARB_PROVIDER, RD_BARB_MODEL, RD_BARB_FAILOVER (comma list). Defaults to
claude / opus with chain claude -> codex -> antigravity -> cursor-agent.
Only subscription CLIs; never a pay-per-use API.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from staff.provider_failover_config import FailoverStep, resolve_failover_chain  # noqa: E402

from scripts.rd_barb_notify import normalize_role, role_env_prefix  # noqa: E402

DEFAULT_PROVIDER = "claude"
DEFAULT_MODEL = "opus"
DEFAULT_CHAIN = ("claude", "codex", "antigravity", "cursor-agent")
PERSONA_ROLE_CHAINS: dict[str, tuple[FailoverStep, ...]] = {
    "rd-barb": (
        FailoverStep("claude", "opus"),
        FailoverStep("codex", None),
        FailoverStep("antigravity", None),
        FailoverStep("cursor-agent", None),
    ),
    "bob": (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
        FailoverStep("codex", "gpt-6.1-sol"),
    ),
    "pm-gasification": (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
        FailoverStep("codex", "gpt-6.1-sol"),
    ),
    "pm-upstreamdrift": (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
        FailoverStep("codex", "gpt-6.1-sol"),
    ),
    "fleet-dispatcher": (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
        FailoverStep("codex", "gpt-6.1-sol"),
    ),
    "pm-sidekick": (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
        FailoverStep("codex", "gpt-6.1-sol"),
    ),
    "expert-panel": (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
        FailoverStep("codex", "gpt-6.1-sol"),
    ),
    "pm-affinedrift": (
        FailoverStep("claude", "claude-opus-5-5"),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
        FailoverStep("codex", "gpt-6.1-sol"),
    ),
    "pragmatic-programmer": (
        FailoverStep("claude", None),
        FailoverStep("codex", None),
        FailoverStep("antigravity", None),
        FailoverStep("cursor-agent", None),
    ),
}
SWITCH_ON = frozenset({"auth_expired", "rate_limited", "quota_exhausted", "provider_capacity", "crash"})


@dataclass(frozen=True)
class Decision:
    provider: str | None
    model: str | None
    switched: bool
    previous: str | None = None


def chain(config: Mapping[str, str], *, role: str = "rd-barb") -> tuple[FailoverStep, ...]:
    """Ordered failover steps; the head uses RD_<ROLE>_MODEL (default opus for claude)."""
    norm = normalize_role(role)
    prefix = role_env_prefix(norm)
    role_defaults = PERSONA_ROLE_CHAINS.get(norm)
    raw = config.get(f"{prefix}FAILOVER")
    if raw:
        providers = tuple(p.strip() for p in raw.split(",") if p.strip() in DEFAULT_CHAIN) or DEFAULT_CHAIN
        steps = tuple(FailoverStep(p, None) for p in providers)
    elif role_defaults:
        providers = tuple(s.provider for s in role_defaults)
        steps = role_defaults
    else:
        providers = DEFAULT_CHAIN
        steps = tuple(FailoverStep(p, None) for p in providers)
    model = (config.get(f"{prefix}MODEL") or "").strip() or None
    role_obj = SimpleNamespace(providers=providers, model=model, failover_chain=steps)
    requested = (config.get(f"{prefix}PROVIDER") or "").strip() or None
    resolved = resolve_failover_chain(role_obj, requested_provider=requested if requested in providers else None)
    head = resolved[0]
    if head.model is None and model is None and head.provider == DEFAULT_PROVIDER:
        model = DEFAULT_MODEL
    return (FailoverStep(head.provider, model or head.model), *resolved[1:])


def select(
    config: Mapping[str, str],
    current: str | None = None,
    failure_class: str | None = None,
    *,
    role: str = "rd-barb",
    now: float | None = None,
    max_age_s: float | None = None,
) -> Decision:
    """Pick the provider for the next start. Switch only on provider failures."""
    steps = chain(config, role=role)
    if current is None and failure_class is None:
        from staff.rd_barb_provider_state import read_active_provider  # noqa: PLC0415

        snap = read_active_provider(
            normalize_role(role),
            max_age_s=max_age_s,
            now=now,
            allowed_providers=tuple(s.provider for s in steps),
        )
        if snap is not None:
            matched = next((s for s in steps if s.provider == snap.provider), None)
            model = snap.model if snap.model is not None else (matched.model if matched else None)
            return Decision(snap.provider, model, False, snap.previous)
    if current is None:
        return Decision(steps[0].provider, steps[0].model, False)
    idx = next((i for i, s in enumerate(steps) if s.provider == current), None)
    if failure_class not in SWITCH_ON:
        step = steps[idx] if idx is not None else steps[0]
        return Decision(step.provider, step.model, step.provider != current, current)
    later = steps[idx + 1 :] if idx is not None else steps
    if not later:
        return Decision(None, None, True, current)
    return Decision(later[0].provider, later[0].model, True, current)


def switch_notice(
    d: Decision, *, failure_class: str | None, run_id: str | None = None, role: str = "rd-barb"
) -> dict[str, Any] | None:
    """Envelope for rd_barb_notify: update on a switch, alert when nothing is left (#3112, #3295)."""
    if not d.switched:
        return None
    norm = normalize_role(role)
    key = f"{norm}-provider-{d.previous}-{d.provider or 'none'}-{failure_class}"
    base: dict[str, Any] = {"dedupe_key": key, **({"run_id": run_id} if run_id else {})}
    if norm != "rd-barb":
        base["sender_role"] = norm
    if d.provider is None:
        return base | {
            "kind": "alert",
            "urgency": "high",
            "summary": f"{norm} stopped: every provider failed (last {d.previous}, {failure_class})",
            "body": f"No subscription CLI in the chain could start {norm}. Monitoring is paused.",
            "recommended_action": f"Re-authenticate the {DEFAULT_PROVIDER} CLI on Desk, then restart {norm}.",
        }
    return base | {
        "kind": "update",
        "urgency": "normal",
        "summary": f"{norm} switched provider {d.previous} -> {d.provider} ({failure_class})",
        "body": (
            f"{d.previous} hit {failure_class}; restarted on {d.provider}"
            f"{f' ({d.model})' if d.model else ''}.\n"
            f"Recommendation: re-authenticate or wait out {d.previous}; {norm} returns to it on the next "
            "clean start. No action needed if this run stays healthy."
        ),
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    config: Mapping[str, str] | None = None,
    notify: Callable[[dict[str, Any]], Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", default="rd-barb", help="persona role slug (default: rd-barb)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    nxt = sub.add_parser("next")
    nxt.add_argument("--role", dest="sub_role", default=None)
    nxt.add_argument("--current")
    nxt.add_argument("--failure")
    nxt.add_argument("--run-id")
    args = parser.parse_args(argv)
    role = normalize_role(args.sub_role or args.role)
    if config is None or notify is None:
        rn = importlib.import_module("scripts.rd_barb_notify")  # shared config + notify path

        config = rn.load_config(role=role) if config is None else config
        notify = notify or _default_notify(rn, role=role)
    decision = select(config, args.current, args.failure, role=role)
    notice = switch_notice(decision, failure_class=args.failure, run_id=args.run_id, role=role)
    if notice is not None:
        notify(notice)
    if decision.provider:
        from staff.rd_barb_provider_state import record_active_provider  # noqa: PLC0415

        record_active_provider(
            role,
            decision.provider,
            model=decision.model,
            failure_class=args.failure,
            previous=decision.previous,
        )
    print(json.dumps(decision.__dict__, sort_keys=True))
    return 0 if decision.provider else 1


def _default_notify(rn: Any, *, role: str = "rd-barb") -> Callable[[dict[str, Any]], Any]:
    import os  # noqa: PLC0415

    norm = normalize_role(role)
    prefix = role_env_prefix(norm)
    state_env = os.environ.get(f"{prefix}NOTIFY_STATE") or (
        os.environ.get("RD_BARB_NOTIFY_STATE") if norm == "rd-barb" else None
    )
    state = Path(state_env or rn.default_state_path(norm)).expanduser()
    notifier = rn.Notifier(
        rn.load_config(role=norm), state, rn.http_send, rn._fleet_thread_poster(role=norm), role=norm
    )
    return notifier.notify


if __name__ == "__main__":
    sys.exit(main())
