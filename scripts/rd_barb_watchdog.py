#!/usr/bin/env python3
"""rd-barb watchdog: restart her on the next subscription CLI after a provider failure.

  run [--max-restarts N] [--dry-run]

Starts rd-barb with the provider ``rd_barb_provider.py next`` picks, waits for the
session to exit, and classifies the exit with ``staff.classifier.classify_run_failure``
(auth_expired, rate_limited, quota_exhausted, provider_capacity; any other non-zero
exit is ``crash``). On a failure it calls ``rd_barb_provider.py next --current P
--failure CLASS`` and restarts on the provider it returns. ``next`` owns the chain and
sends the "switched provider" fyi with a recommendation, or the alert with a
recommended_action when the chain is exhausted, so this file never builds a message.

Config (env or the git-ignored rd-barb.env): RD_BARB_LAUNCH_CMD, a command template
with ``{provider}`` and ``{model}`` (model may be empty). With it unset the watchdog
fails closed (exit 2) and launches nothing. Never starts a pay-per-use API.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import shlex
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from staff.classifier import classify_run_failure  # noqa: E402

log = logging.getLogger("rd_barb.watchdog")

PROVIDER_SCRIPT = _ROOT / "scripts" / "rd_barb_provider.py"
PROVIDER_FAILURES = frozenset({"auth_expired", "rate_limited", "quota_exhausted", "provider_capacity"})
BACKOFF_STEP_S = 30.0
MAX_BACKOFF_S = 300.0
DEFAULT_MAX_RESTARTS = 8
TAIL_BYTES = 64 * 1024


class WatchdogError(RuntimeError):
    """Fail-closed condition: missing config or an unreadable ``next`` answer."""


@dataclass(frozen=True)
class Pick:
    provider: str | None
    model: str | None


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    output: str = ""


class NextProvider(Protocol):
    def __call__(self, current: str | None = None, failure: str | None = None, run_id: str | None = None) -> Pick: ...


def classify(provider: str, result: RunResult) -> str | None:
    """None for a clean exit; a provider failure class; otherwise ``crash``."""
    if result.exit_code == 0:
        return None
    verdict = classify_run_failure(provider, result.exit_code, output_text=result.output)
    return verdict.failure_class if verdict.failure_class in PROVIDER_FAILURES else "crash"


def backoff_seconds(restarts: int) -> float:
    return min(BACKOFF_STEP_S * max(restarts, 1), MAX_BACKOFF_S)


def tail(text: str, limit: int = TAIL_BYTES) -> str:
    return text[-limit:]


def supervise(
    *,
    launch: Callable[[str, str | None], RunResult],
    next_provider: NextProvider,
    max_restarts: int = DEFAULT_MAX_RESTARTS,
    sleep: Callable[[float], Any] = time.sleep,
    run_id: str | None = None,
) -> int:
    """Run until a clean exit (0), an exhausted chain or the restart cap (1)."""
    pick = next_provider()
    restarts = 0
    while pick.provider:
        failure = classify(pick.provider, launch(pick.provider, pick.model))
        if failure is None:
            return 0
        if restarts >= max_restarts:
            log.error("rd-barb restart cap %d reached on %s (%s)", max_restarts, pick.provider, failure)
            return 1
        log.warning("rd-barb %s exited with %s; asking rd_barb_provider next", pick.provider, failure)
        pick = next_provider(current=pick.provider, failure=failure, run_id=run_id)
        if pick.provider:
            restarts += 1
            sleep(backoff_seconds(restarts))
    log.error("rd-barb provider chain exhausted; next sent the alert")
    return 1


def cli_next(
    current: str | None = None,
    failure: str | None = None,
    run_id: str | None = None,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Pick:
    """Ask ``rd_barb_provider.py next``; it also sends the switched-provider notice."""
    cmd = [sys.executable, str(PROVIDER_SCRIPT), "next"]
    for flag, value in (("--current", current), ("--failure", failure), ("--run-id", run_id)):
        if value:
            cmd += [flag, value]
    proc = run(cmd, capture_output=True, text=True, timeout=120, check=False)
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    try:
        data = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise WatchdogError(f"rd_barb_provider next gave no JSON (rc={proc.returncode})") from exc
    if not isinstance(data, dict) or "provider" not in data:
        raise WatchdogError("rd_barb_provider next JSON has no provider")
    return Pick(data["provider"] or None, data.get("model") or None)


def launch_command(config: Mapping[str, str], provider: str, model: str | None) -> list[str]:
    template = (config.get("RD_BARB_LAUNCH_CMD") or "").strip()
    if not template:
        raise WatchdogError("RD_BARB_LAUNCH_CMD is not set; refusing to start rd-barb")
    return [part.format(provider=provider, model=model or "") for part in shlex.split(template)]


def subprocess_launcher(config: Mapping[str, str]) -> Callable[[str, str | None], RunResult]:
    def launch(provider: str, model: str | None) -> RunResult:
        env = os.environ | {"RD_BARB_ACTIVE_PROVIDER": provider, "RD_BARB_ACTIVE_MODEL": model or ""}
        proc = subprocess.Popen(  # noqa: S603 - operator-configured command, no shell
            launch_command(config, provider, model),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        buf = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            buf = tail(buf + line)
        return RunResult(proc.wait(), buf)

    return launch


def _load_config(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The git-ignored rd-barb.env (via rd_barb_notify), then non-empty env values win."""
    env = os.environ if env is None else env
    try:
        cfg = dict(importlib.import_module("scripts.rd_barb_notify").load_config(env))
    except (ImportError, AttributeError):
        cfg = {}
    cfg.update({k: v for k, v in env.items() if k.startswith("RD_BARB_") and v})
    return cfg


def main(
    argv: Sequence[str] | None = None,
    *,
    config: Mapping[str, str] | None = None,
    next_provider: NextProvider | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--max-restarts", type=int, default=DEFAULT_MAX_RESTARTS)
    run_p.add_argument("--dry-run", action="store_true", help="print the first command; launch nothing")
    args = parser.parse_args(argv)
    cfg = _load_config() if config is None else config
    nxt = next_provider or cli_next
    try:
        if args.dry_run:
            first = nxt()
            print(shlex.join(launch_command(cfg, first.provider or "", first.model)))
            return 0
        launch_command(cfg, "claude", None)  # fail closed before any start
        return supervise(
            launch=subprocess_launcher(cfg),
            next_provider=nxt,
            max_restarts=args.max_restarts,
            run_id=f"rd-barb-{uuid.uuid4().hex[:8]}",
        )
    except WatchdogError as exc:
        print(f"rd_barb_watchdog: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
