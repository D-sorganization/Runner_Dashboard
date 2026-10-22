"""Alexandria ``barb lib`` subcommands (#2165 slice 8)."""

from __future__ import annotations

import argparse
import json
import re

from fleet_client import FleetArgumentError, FleetClient

_REF_RE = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+)#(?P<num>\d+)$")


def parse_requesting_ref(ref: str) -> tuple[str, int]:
    match = _REF_RE.match(ref.strip())
    if not match:
        raise FleetArgumentError("requesting_ref must look like Repo#issue")
    return match.group("repo"), int(match.group("num"))


def cmd_lib_search(client: FleetClient, args: argparse.Namespace) -> int:
    result = client.library_search(
        args.query,
        repo=args.repo or None,
        topic=args.topic or None,
        budget=args.budget or None,
        k=args.k or None,
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_lib_get(client: FleetClient, work_id: int) -> int:
    print(json.dumps(client.library_work(work_id), indent=2))
    return 0


def cmd_lib_submit(client: FleetClient, args: argparse.Namespace) -> int:
    authors = [a.strip() for a in args.authors.split(",") if a.strip()]
    payload = {
        "doi": args.doi or None,
        "url": args.url or None,
        "isbn": args.isbn or None,
        "title": args.title,
        "authors": authors,
        "reason": args.reason,
        "requesting_ref": args.requesting_ref,
        "excerpt": args.excerpt or None,
    }
    created = client.library_submit(payload)
    print(json.dumps(created, indent=2))
    if args.notify and isinstance(created, dict) and created.get("id"):
        repo, issue = parse_requesting_ref(args.requesting_ref)
        note = f"Library submission #{created['id']} ({created.get('status', 'pending')}) for {args.title!r}."
        client.agent_bus_send(
            to="alexandria",
            repo=repo,
            work_kind="issue",
            work_number=issue,
            body=note,
            msg_type="request",
        )
    return 0


def register_lib_commands(sub: argparse._SubParsersAction) -> None:
    lib = sub.add_parser("lib", help="Alexandria library search and submissions.")
    lib_sub = lib.add_subparsers(dest="lib_cmd", required=True)

    search_p = lib_sub.add_parser("search", help="Search passages in the fleet library.")
    search_p.add_argument("query")
    search_p.add_argument("--repo", default="")
    search_p.add_argument("--topic", default="")
    search_p.add_argument("--budget", type=int, default=0)
    search_p.add_argument("--k", type=int, default=0)

    get_p = lib_sub.add_parser("get", help="Fetch one catalogued work by id.")
    get_p.add_argument("work_id", type=int)

    submit_p = lib_sub.add_parser("submit", help="File a reference for Alexandria review.")
    submit_p.add_argument("--doi", default="")
    submit_p.add_argument("--url", default="")
    submit_p.add_argument("--isbn", default="")
    submit_p.add_argument("--title", required=True)
    submit_p.add_argument("--authors", required=True, help="Comma-separated author names")
    submit_p.add_argument("--reason", required=True)
    submit_p.add_argument("--requesting-ref", dest="requesting_ref", required=True)
    submit_p.add_argument("--excerpt", default="")
    submit_p.add_argument(
        "--notify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Notify alexandria on the agent bus (default: on)",
    )
