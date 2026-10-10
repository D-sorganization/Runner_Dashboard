#!/usr/bin/env python3
"""fleetctl - command-line client for the Runner Dashboard Fleet API (#1228).

Every subcommand is generated from the shared table in ``fleet_tools.py`` and prints the
API's JSON response on stdout. Exit codes: 0 success, 1 API error (error JSON on stdout),
2 invalid arguments (error JSON on stdout; nothing was sent).

Examples::

    python3 fleetctl.py briefing --repo Runner_Dashboard --agent grok
    python3 fleetctl.py claim --repo Runner_Dashboard --issue 1228 --intent "fleet clients"
    python3 fleetctl.py dispatch night-watch --repo Runner_Dashboard --prompt "sweep" --dry-run
    python3 fleetctl.py set-directives --directives '[{"text": "Ship #1192", "priority": 1}]'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_client import FleetAPIError, FleetArgumentError, FleetClient, run_doctor  # noqa: E402
from fleet_tools import BY_CLI, COMMANDS, Command  # noqa: E402


def _json_arg(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc


def _add_argument(parser: argparse.ArgumentParser, name: str, schema: dict[str, Any], positional: bool) -> None:
    kind = schema.get("type")
    items = schema.get("items", {})
    kwargs: dict[str, Any] = {"help": schema.get("description")}
    if kind == "integer":
        kwargs["type"] = int
    elif kind == "number":
        kwargs["type"] = float
    elif kind == "boolean":
        kwargs["action"] = "store_true"
        kwargs["default"] = None
    elif kind == "array" and items.get("type") == "string":
        kwargs["action"] = "append"
    elif kind in ("object", "array"):
        kwargs["type"] = _json_arg
        kwargs["metavar"] = "JSON"
    if "enum" in schema:
        kwargs["choices"] = schema["enum"]
    if positional:
        parser.add_argument(name, **kwargs)
    else:
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleetctl", description="Runner Dashboard Fleet API client (JSON output).")
    parser.add_argument("--url", help="API base URL (default $FLEET_API_URL or http://127.0.0.1:8321).")
    parser.add_argument("--timeout", type=float, help="Request timeout in seconds (default $FLEET_API_TIMEOUT or 30).")
    parser.add_argument("--as-agent", dest="default_agent", help="Default agent (default $FLEET_AGENT).")
    parser.add_argument("--as-session", dest="default_session", help="Default session (default $FLEET_SESSION).")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command in COMMANDS:
        cmd_parser = sub.add_parser(command.cli, help=command.description, description=command.description)
        for name, schema in command.properties.items():
            _add_argument(cmd_parser, name, schema, positional=name in command.positional)
    doctor = sub.add_parser(
        "doctor",
        help="Read-only connection diagnostics (DNS/TLS, token, scopes, version, briefing freshness).",
        description="Diagnose the agent connection without writing anything. Exit 1 if any check fails.",
    )
    doctor.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    doctor.add_argument(
        "--with-roundtrip", action="store_true", help="Also open a Barb thread (a WRITE; off by default)."
    )
    return parser


def _doctor(client: FleetClient, namespace: argparse.Namespace) -> int:
    results = run_doctor(client, with_roundtrip=namespace.with_roundtrip)
    ok = all(result.ok for result in results)
    if namespace.json:
        _emit({"ok": ok, "checks": [result.to_dict() for result in results]})
    else:
        for result in results:
            sys.stdout.write(f"{'PASS' if result.ok else 'FAIL'} {result.name}: {result.detail}\n")
            if not result.ok:
                sys.stdout.write(f"     remedy: {result.remedy}\n")
    return 0 if ok else 1


def _arguments(command: Command, namespace: argparse.Namespace) -> dict[str, Any]:
    return {name: getattr(namespace, name) for name in command.properties if getattr(namespace, name) is not None}


def _emit(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    if namespace.command == "doctor":
        try:
            client = FleetClient(namespace.url, timeout=namespace.timeout)
        except FleetArgumentError as exc:
            _emit({"error": "invalid_arguments", "message": str(exc)})
            return 2
        return _doctor(client, namespace)
    command = BY_CLI[namespace.command]
    try:
        client = FleetClient(
            namespace.url, timeout=namespace.timeout, agent=namespace.default_agent, session=namespace.default_session
        )
        _emit(command.invoke(client, _arguments(command, namespace)))
        if client.identity()["session_source"] == "derived" and client.session:
            sys.stderr.write(f"fleetctl: derived session; export FLEET_SESSION={client.session} to reuse it\n")
    except FleetArgumentError as exc:
        _emit({"error": "invalid_arguments", "message": str(exc)})
        return 2
    except FleetAPIError as exc:
        _emit(exc.to_dict())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
