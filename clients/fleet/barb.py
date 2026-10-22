#!/usr/bin/env python3
# ruff: noqa: T201
"""Portable Barb CLI for any coding agent (#2157 slice 6).

Usage:
  python3 clients/fleet/barb.py ask "How should I split this epic?"
  python3 clients/fleet/barb.py status
  python3 clients/fleet/barb.py project new --title T --goal G --repo Runner_Dashboard
  python3 clients/fleet/barb.py dispatch issue-remediator Runner_Dashboard --prompt "Fix CI"
  python3 clients/fleet/barb.py lib search "hip torque golf" --repo UpstreamDrift --budget 1200
  python3 clients/fleet/barb.py lib get 42
  python3 clients/fleet/barb.py lib submit --doi 10.5555/x --title T --authors "A" --reason R --requesting-ref UpstreamDrift#1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from barb_lib import cmd_lib_get, cmd_lib_search, cmd_lib_submit, register_lib_commands  # noqa: E402
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient  # noqa: E402


def _client() -> FleetClient:
    return FleetClient(agent="barb")


def cmd_ask(client: FleetClient, message: str) -> int:
    thread = client.staff_thread_open(role="barb", title="Ask Barb", initial_message=message.strip(), kind="auto")
    tid = thread.get("id") if isinstance(thread, dict) else None
    if not tid:
        print(json.dumps(thread, indent=2))
        return 1
    waited = client.staff_thread_wait(tid, since_seq=0, timeout=90.0)
    print(json.dumps(waited, indent=2))
    return 0


def cmd_status(client: FleetClient) -> int:
    print(json.dumps(client.briefing(), indent=2))
    return 0


def cmd_project_new(client: FleetClient, args: argparse.Namespace) -> int:
    repos = [args.repo] if args.repo else []
    created = client.staff_project_create(args.title, args.goal, definition_of_done=args.done or "", repos=repos)
    if args.decompose and isinstance(created, dict) and created.get("id"):
        created = client.staff_project_decompose(str(created["id"]))
    print(json.dumps(created, indent=2))
    return 0


def cmd_dispatch(client: FleetClient, args: argparse.Namespace) -> int:
    body = {
        "role": args.role,
        "repo": args.repo,
        "prompt": args.prompt,
        "machine": "local",
    }
    if args.issue:
        body["issue"] = int(args.issue)
    result = client.request("POST", f"/api/staff/{args.role}/run", body=body)
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="barb", description="Ask Barb and fleet project intake from any agent app.")
    sub = parser.add_subparsers(dest="command", required=True)

    ask_p = sub.add_parser("ask", help="Send Barb a question on the agent bus thread.")
    ask_p.add_argument("message", help="Question for Barb")

    sub.add_parser("status", help="Fleet briefing snapshot.")

    proj = sub.add_parser("project", help="Project intake.")
    proj_sub = proj.add_subparsers(dest="project_cmd", required=True)
    new_p = proj_sub.add_parser("new", help="Create a tracked project.")
    new_p.add_argument("--title", required=True)
    new_p.add_argument("--goal", required=True)
    new_p.add_argument("--repo", default="")
    new_p.add_argument("--done", default="", help="Definition of done")
    new_p.add_argument("--decompose", action="store_true", help="Run decomposition immediately")

    disp = sub.add_parser("dispatch", help="Request a staff dispatch (Barb policy still applies).")
    disp.add_argument("role")
    disp.add_argument("repo")
    disp.add_argument("--prompt", required=True)
    disp.add_argument("--issue", type=int, default=0)

    register_lib_commands(sub)

    args = parser.parse_args(argv)
    try:
        client = _client()
    except FleetArgumentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        if args.command == "ask":
            return cmd_ask(client, args.message)
        if args.command == "status":
            return cmd_status(client)
        if args.command == "project" and args.project_cmd == "new":
            return cmd_project_new(client, args)
        if args.command == "dispatch":
            return cmd_dispatch(client, args)
        if args.command == "lib" and args.lib_cmd == "search":
            return cmd_lib_search(client, args)
        if args.command == "lib" and args.lib_cmd == "get":
            return cmd_lib_get(client, args.work_id)
        if args.command == "lib" and args.lib_cmd == "submit":
            return cmd_lib_submit(client, args)
    except FleetAPIError as exc:
        print(json.dumps(exc.to_envelope(), indent=2), file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
