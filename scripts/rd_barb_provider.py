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
claude / claude-opus-5-5 with chain claude -> codex -> antigravity -> cursor-agent.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from staff.provider_failover_config import FailoverStep, resolve_failover_chain  # noqa: E402

DEFAULT_PROVIDER = "claude"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_CHAIN = ("claude", "codex", "antigravity", "cursor-agent")
SWITCH_ON = frozenset({"auth_expired", "rate_limited", "quota_exhausted", "provider_capacity", "crash"})


@dataclass(frozen=True)
class Decision:
    provider: str | None
    model: str | None
    switched: bool
    previous: str | None = None


def chain(config: Mapping[str, str]) -> tuple[FailoverStep, ...]:
    """Ordered failover steps; the head uses RD_BARB_MODEL (default Opus 5.5)."""
    raw = config.get("RD_BARB_FAILOVER") or ",".join(DEFAULT_CHAIN)
    providers = tuple(p.strip() for p in raw.split(",") if p.strip() in DEFAULT_CHAIN) or DEFAULT_CHAIN
    model = (config.get("RD_BARB_MODEL") or "").strip() or None
    steps = tuple(FailoverStep(p, None) for p in providers)
    role = SimpleNamespace(providers=providers, model=model, failover_chain=steps)
    requested = (config.get("RD_BARB_PROVIDER") or "").strip() or None
    resolved = resolve_failover_chain(role, requested_provider=requested if requested in providers else None)
    head = resolved[0]
    if head.model is None and model is None and head.provider == DEFAULT_PROVIDER:
        model = DEFAULT_MODEL
    return (FailoverStep(head.provider, model or head.model), *resolved[1:])


def select(config: Mapping[str, str], current: str | None = None, failure_class: str | None = None) -> Decision:
    """Pick the provider for the next start. Switch only on provider failures."""
    steps = chain(config)
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


def switch_notice(d: Decision, *, failure_class: str | None, run_id: str | None = None) -> dict[str, Any] | None:
    """Envelope for rd_barb_notify: update on a switch, alert when nothing is left (#3112)."""
    if not d.switched:
        return None
    key = f"rd-barb-provider-{d.previous}-{d.provider or 'none'}-{failure_class}"
    base = {"dedupe_key": key, "run_id": run_id} if run_id else {"dedupe_key": key}
    if d.provider is None:
        return base | {
            "kind": "alert",
            "urgency": "high",
            "summary": f"rd-barb stopped: every provider failed (last {d.previous}, {failure_class})",
            "body": "No subscription CLI in the chain could start rd-barb. Monitoring is paused.",
            "recommended_action": f"Re-authenticate the {DEFAULT_PROVIDER} CLI on Desk, then restart rd-barb.",
        }
    return base | {
        "kind": "update",
        "urgency": "normal",
        "summary": f"rd-barb switched provider {d.previous} -> {d.provider} ({failure_class})",
        "body": (
            f"{d.previous} hit {failure_class}; restarted on {d.provider}"
            f"{f' ({d.model})' if d.model else ''}.\n"
            f"Recommendation: re-authenticate or wait out {d.previous}; rd-barb returns to it on the next "
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
    sub = parser.add_subparsers(dest="cmd", required=True)
    nxt = sub.add_parser("next")
    nxt.add_argument("--current")
    nxt.add_argument("--failure")
    nxt.add_argument("--run-id")
    args = parser.parse_args(argv)
    if config is None or notify is None:
        rn = importlib.import_module("scripts.rd_barb_notify")  # shared config + notify path

        config = rn.load_config() if config is None else config
        notify = notify or _default_notify(rn)
    decision = select(config, args.current, args.failure)
    notice = switch_notice(decision, failure_class=args.failure, run_id=args.run_id)
    if notice is not None:
        notify(notice)
    print(json.dumps(decision.__dict__, sort_keys=True))
    return 0 if decision.provider else 1


def _default_notify(rn: Any) -> Callable[[dict[str, Any]], Any]:
    import os  # noqa: PLC0415

    state = Path(os.environ.get("RD_BARB_NOTIFY_STATE") or rn.DEFAULT_STATE).expanduser()
    notifier = rn.Notifier(rn.load_config(), state, rn.http_send, rn._fleet_thread_poster())
    return notifier.notify


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.exit(main())
