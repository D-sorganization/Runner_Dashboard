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
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from staff.classifier import classify_run_failure  # noqa: E402

from scripts.rd_barb_notify import normalize_role, role_env_prefix  # noqa: E402

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


def cap_alert(
    provider: str, failure: str, max_restarts: int, run_id: str | None, *, role: str = "rd-barb"
) -> dict[str, Any]:
    """rd_barb_notify alert envelope for a session that keeps failing on one provider."""
    norm = normalize_role(role)
    msg: dict[str, Any] = {
        "kind": "alert",
        "urgency": "high",
        "dedupe_key": f"{norm}-restart-cap-{provider}-{failure}",
        "summary": f"{norm} stopped: restart cap {max_restarts} hit on {provider} ({failure})",
        "body": f"The watchdog restarted {norm} repeatedly without a clean exit. Monitoring is paused.",
        "recommended_action": f"Check the {provider} session log on Desk, fix the cause, then restart {norm}.",
    }
    if norm != "rd-barb":
        msg["sender_role"] = norm
    return msg | {"run_id": run_id} if run_id else msg


def _send_alert(alert: Callable[[dict[str, Any]], Any] | None, msg: dict[str, Any]) -> None:
    if alert is None:
        return
    try:
        alert(msg)
    except Exception:  # noqa: BLE001 - alerting must never take the watchdog down
        log.exception("rd-barb restart-cap alert failed")


def supervise(
    *,
    launch: Callable[[str, str | None], RunResult],
    next_provider: NextProvider,
    max_restarts: int = DEFAULT_MAX_RESTARTS,
    sleep: Callable[[float], Any] = time.sleep,
    run_id: str | None = None,
    alert: Callable[[dict[str, Any]], Any] | None = None,
    role: str = "rd-barb",
) -> int:
    """Run until a clean exit (0), an exhausted chain or the restart cap (1)."""
    norm = normalize_role(role)
    pick = next_provider()
    restarts = 0
    while pick.provider:
        failure = classify(pick.provider, launch(pick.provider, pick.model))
        if failure is None:
            return 0
        if restarts >= max_restarts:
            log.error("rd-barb restart cap %d reached on %s (%s)", max_restarts, pick.provider, failure)
            _send_alert(alert, cap_alert(pick.provider, failure, max_restarts, run_id, role=norm))
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
    role: str = "rd-barb",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Pick:
    """Ask ``rd_barb_provider.py next``; it also sends the switched-provider notice."""
    norm = normalize_role(role)
    cmd = [sys.executable, str(PROVIDER_SCRIPT), "next"]
    if norm != "rd-barb":
        cmd += ["--role", norm]
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


def launch_command(config: Mapping[str, str], provider: str, model: str | None, *, role: str = "rd-barb") -> list[str]:
    norm = normalize_role(role)
    key = f"{role_env_prefix(norm)}LAUNCH_CMD"
    template = (config.get(key) or "").strip()
    if not template:
        raise WatchdogError(f"{key} is not set; refusing to start {norm}")
    return [part.format(provider=provider, model=model or "", role=norm) for part in shlex.split(template)]


def subprocess_launcher(config: Mapping[str, str], *, role: str = "rd-barb") -> Callable[[str, str | None], RunResult]:
    norm, prefix = normalize_role(role), role_env_prefix(role)

    def launch(provider: str, model: str | None) -> RunResult:
        extra = {f"{prefix}ACTIVE_PROVIDER": provider, f"{prefix}ACTIVE_MODEL": model or ""}
        if norm != "rd-barb":
            extra["RD_PERSONA_ROLE"] = norm
        env = os.environ | extra
        proc = subprocess.Popen(  # noqa: S603 - operator-configured command, no shell
            launch_command(config, provider, model, role=norm),
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


def _load_config(env: Mapping[str, str] | None = None, *, role: str = "rd-barb") -> dict[str, str]:
    """The git-ignored rd-<role>.env (via rd_barb_notify), then non-empty env values win."""
    env = os.environ if env is None else env
    norm, prefix = normalize_role(role), role_env_prefix(role)
    try:
        cfg = dict(importlib.import_module("scripts.rd_barb_notify").load_config(env, role=norm))
    except (ImportError, AttributeError):
        cfg = {}
    cfg.update({k: v for k, v in env.items() if k.startswith(prefix) and k != f"{prefix}CONFIG" and v})
    return cfg


def _default_alert(role: str = "rd-barb") -> Callable[[dict[str, Any]], Any] | None:
    """rd_barb_notify's Notifier, the single path for persona messages to Grok Barb."""
    norm, prefix = normalize_role(role), role_env_prefix(role)
    try:
        rn = importlib.import_module("scripts.rd_barb_notify")
    except ImportError:
        log.warning("scripts.rd_barb_notify missing; restart-cap alert disabled")
        return None
    state_env = os.environ.get(f"{prefix}NOTIFY_STATE") or (
        os.environ.get("RD_BARB_NOTIFY_STATE") if norm == "rd-barb" else None
    )
    state = Path(state_env or rn.default_state_path(norm)).expanduser()
    return rn.Notifier(
        rn.load_config(role=norm), state, rn.http_send, rn._fleet_thread_poster(role=norm), role=norm
    ).notify


def main(
    argv: Sequence[str] | None = None,
    *,
    config: Mapping[str, str] | None = None,
    next_provider: NextProvider | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--role", default="rd-barb", help="persona role slug (default: rd-barb)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--role", dest="sub_role", default=None)
    run_p.add_argument("--max-restarts", type=int, default=DEFAULT_MAX_RESTARTS)
    run_p.add_argument("--dry-run", action="store_true", help="print the first command; launch nothing")
    args = parser.parse_args(argv)
    role = normalize_role(args.sub_role or args.role)
    cfg = _load_config(role=role) if config is None else config
    nxt: NextProvider = next_provider or (
        lambda current=None, failure=None, run_id=None: cli_next(current, failure, run_id, role=role)
    )
    try:
        if args.dry_run:
            first = nxt()
            print(shlex.join(launch_command(cfg, first.provider or "", first.model, role=role)))
            return 0
        launch_command(cfg, "claude", None, role=role)  # fail closed before any start
        return supervise(
            launch=subprocess_launcher(cfg, role=role),
            next_provider=nxt,
            max_restarts=args.max_restarts,
            run_id=f"{role}-{uuid.uuid4().hex[:8]}",
            alert=_default_alert(role=role),
            role=role,
        )
    except WatchdogError as exc:
        print(f"rd_barb_watchdog: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
