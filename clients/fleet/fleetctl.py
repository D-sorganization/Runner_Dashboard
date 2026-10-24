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

from fleet_client import (  # noqa: E402
    MAX_VERIFICATION_CHARACTERS,
    FleetAPIError,
    FleetArgumentError,
    FleetClient,
    run_doctor,
)
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


def _add_library_operations(parser: argparse.ArgumentParser) -> None:
    """Expose ergonomic aliases using the unified query schema and implementation."""
    query = BY_CLI["query"]
    operations = parser.add_subparsers(dest="library_operation", required=True)
    for operation, positional, optional in (
        ("search", "query", ("repo", "topic", "k", "offset", "full", "run_id", "scope")),
        ("sources", None, ()),
    ):
        child = operations.add_parser(operation, help=f"Read-only library {operation}.")
        defaults: dict[str, Any] = {name: None for name in query.properties}
        defaults.update(source="library", op=operation)
        child.set_defaults(command="query", **defaults)
        if positional is not None:
            _add_argument(child, positional, query.properties[positional], positional=True)
        for name in optional:
            _add_argument(child, name, query.properties[name], positional=False)
    fetch = operations.add_parser("fetch", help="Fetch a cited passage or a source/work/page locator.")
    fetch.add_argument("cite", help="Canonical citation, or source id when work id and page are supplied.")
    fetch.add_argument("work_id", nargs="?", type=int, help="Work id for a source/work/page locator.")
    fetch.add_argument("--page", type=int, help="One-based page number for a source/work/page locator.")
    fetch.add_argument("--scope", choices=("local", "federated"), help="Locator scope (defaults to local).")
    fetch.add_argument("--node", help="Optional registered node for a locator.")
    _add_argument(fetch, "run_id", query.properties["run_id"], positional=False)
    fetch_defaults: dict[str, Any] = {name: None for name in query.properties}
    fetch_defaults.update(source="library", op="fetch", work_id=None, page=None, scope=None, node=None)
    fetch.set_defaults(command="query", **fetch_defaults)
    verify = operations.add_parser("verify", help="Verify citations and links in a UTF-8 file.")
    verify.set_defaults(command="library-verify", content=None)
    verify.add_argument("--file", dest="verification_file", required=True, type=Path)
    _add_argument(verify, "run_id", BY_CLI["library-verify"].properties["run_id"], positional=False)
    _add_argument(verify, "repo", BY_CLI["library-verify"].properties["repo"], positional=False)


def _add_barb_seat(sub: Any) -> None:
    """``fleetctl barb seat {status,checkout,heartbeat,turnover,release}``."""
    barb = sub.add_parser("barb", help="Barb seat tools.").add_subparsers(dest="barb_group", required=True)
    seat = barb.add_parser("seat", help="Take, hold and hand over a Barb seat.").add_subparsers(
        dest="seat_action", required=True, metavar="ACTION"
    )
    for command in COMMANDS:
        if not command.cli.startswith("barb seat "):
            continue
        child = seat.add_parser(command.cli.rsplit(" ", 1)[1], help=command.description)
        child.set_defaults(command=command.cli)
        for name, schema in command.properties.items():
            _add_argument(child, name, schema, positional=name in command.positional)


def _add_exclusions(sub: Any) -> None:
    """``fleetctl exclusions {list,add,remove}``."""
    group = sub.add_parser("exclusions", help="Dispatch exclusions.").add_subparsers(
        dest="exclusion_action", required=True, metavar="ACTION"
    )
    for command in COMMANDS:
        if not command.cli.startswith("exclusions "):
            continue
        child = group.add_parser(command.cli.split(" ", 1)[1], help=command.description)
        child.set_defaults(command=command.cli)
        for name, schema in command.properties.items():
            _add_argument(child, name, schema, positional=name in command.positional)


def _add_memory_group(sub: Any) -> None:
    """``fleetctl memory brief`` (grouped commands whose cli name starts with ``memory ``)."""
    memory = sub.add_parser("memory", help="Shared memory helpers.").add_subparsers(
        dest="memory_action", required=True, metavar="ACTION"
    )
    for command in COMMANDS:
        if not command.cli.startswith("memory "):
            continue
        child = memory.add_parser(command.cli.split(" ", 1)[1], help=command.description)
        child.set_defaults(command=command.cli)
        for name, schema in command.properties.items():
            _add_argument(child, name, schema, positional=name in command.positional)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleetctl", description="Runner Dashboard Fleet API client (JSON output).")
    parser.add_argument("--url", help="API base URL (default $FLEET_API_URL or http://127.0.0.1:8321).")
    parser.add_argument("--timeout", type=float, help="Request timeout in seconds (default $FLEET_API_TIMEOUT or 30).")
    parser.add_argument("--as-agent", dest="default_agent", help="Default agent (default $FLEET_AGENT).")
    parser.add_argument("--as-session", dest="default_session", help="Default session (default $FLEET_SESSION).")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command in COMMANDS:
        if " " in command.cli:  # grouped commands (``barb seat ...``) are registered below
            continue
        cmd_parser = sub.add_parser(command.cli, help=command.description, description=command.description)
        for name, schema in command.properties.items():
            _add_argument(cmd_parser, name, schema, positional=name in command.positional)
    _add_barb_seat(sub)
    _add_exclusions(sub)
    _add_memory_group(sub)
    _add_library_operations(sub.add_parser("library", help="Read-only library search, fetch and source discovery."))
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
        if getattr(namespace, "verification_file", None) is not None:
            try:
                with namespace.verification_file.open(encoding="utf-8", newline="") as document:
                    namespace.content = document.read(MAX_VERIFICATION_CHARACTERS + 1)
            except (OSError, UnicodeError) as exc:
                raise FleetArgumentError("verification file must be readable UTF-8") from exc
        client = FleetClient(
            namespace.url, timeout=namespace.timeout, agent=namespace.default_agent, session=namespace.default_session
        )
        if namespace.command == "query" and getattr(namespace, "library_operation", None) == "fetch":
            work_id = namespace.work_id
            page = namespace.page
            if (work_id is None) != (page is None):
                raise FleetArgumentError("source/work/page locator requires both work id and --page")
            if work_id is None:
                if namespace.scope is not None or namespace.node is not None:
                    raise FleetArgumentError("--scope and --node require a source/work/page locator")
                result = command.invoke(client, _arguments(command, namespace))
            else:
                locator_command = BY_CLI["library-fetch-page"]
                result = locator_command.invoke(
                    client,
                    {
                        "source_id": namespace.cite,
                        "work_id": work_id,
                        "page": page,
                        "scope": namespace.scope,
                        "node": namespace.node,
                        "run_id": namespace.run_id,
                    },
                )
        else:
            result = command.invoke(client, _arguments(command, namespace))
        _emit(result)
        if client.identity()["session_source"] == "derived" and client.session:
            sys.stderr.write(f"fleetctl: derived session; export FLEET_SESSION={client.session} to reuse it\n")
        if command.cli == "library-verify" and not result["verified"]:
            return 1
    except FleetArgumentError as exc:
        _emit({"error": "invalid_arguments", "message": str(exc)})
        return 2
    except FleetAPIError as exc:
        _emit(exc.to_dict())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
