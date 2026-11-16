"""Barb continuity, scoped lease checkout, turnover log, task handoff & report cards CLI helpers."""

# ruff: noqa: T201
from __future__ import annotations

import argparse
import json
from typing import Any

from fleet_client import FleetClient


def register_ops_commands(sub: Any) -> None:
    sub.add_parser("continuity", help="Show multi-Barb scoped leases, turnover log, compliance & task inventory.")

    co = sub.add_parser("checkout", help="Check out a scoped Barb sub-role lease (e.g. barb:front-door).")
    co.add_argument("--scope", default="barb:front-door")
    co.add_argument("--holder", default="", help="Optional; the hub derives it from your token.")
    co.add_argument("--persona", default="rd-barb")
    co.add_argument("--ttl", type=int, default=14400)

    hb = sub.add_parser("heartbeat", help="Extend a live Barb lease (requires epoch).")
    hb.add_argument("--scope", required=True)
    hb.add_argument("--epoch", type=int, required=True)
    hb.add_argument("--holder", default="")
    hb.add_argument("--ttl", type=int, default=14400)

    rn = sub.add_parser("renew", help="One-call renew or re-acquire a Barb lease (no epoch).")
    rn.add_argument("--scope", default="barb:front-door")
    rn.add_argument("--holder", default="")
    rn.add_argument("--persona", default="")
    rn.add_argument("--machine", default="")
    rn.add_argument("--ttl", type=int, default=14400)
    rn.add_argument("--summary", default="")

    hl = sub.add_parser(
        "handoff-lease",
        help="Atomic lease turnover with release, transfer to next_holder, or takeover.",
    )
    hl.add_argument("--scope", default="barb:front-door")
    hl.add_argument("--holder", default="")
    hl.add_argument("--persona", default="rd-barb")
    hl.add_argument("--summary", required=True)
    hl.add_argument("--completed", action="append", default=[])
    hl.add_argument("--in-progress", action="append", default=[])
    hl.add_argument("--next-action", action="append", default=[])
    hl.add_argument("--blocker", action="append", default=[])
    hl.add_argument("--next-holder", default="")
    hl.add_argument("--next-persona", default="")
    hl.add_argument("--next-machine", default="")
    hl.add_argument("--takeover", action="store_true")
    hl.add_argument("--reason", default="")
    hl.add_argument("--ttl", type=int, default=14400)
    hl.add_argument("--mode", default="")
    co.add_argument("--summary", default="")
    co.add_argument("--takeover", action="store_true", help="Operator only; requires --reason.")
    co.add_argument("--reason", default="")

    rel = sub.add_parser("release", help="Release a scoped Barb lease (enforces turnover compliance).")
    rel.add_argument("--scope", required=True)
    rel.add_argument("--holder", default="", help="Optional; the hub derives it from your token.")
    rel.add_argument("--summary", default="", help="Turnover summary if not already logged during shift.")
    rel.add_argument("--force", action="store_true")

    to = sub.add_parser("turnover", help="Append or list shift turnover log entries.")
    to.add_argument("--scope", default="barb:front-door")
    to.add_argument("--persona", default="rd-barb")
    to.add_argument("--holder", default="")
    to.add_argument("--summary", default="")
    to.add_argument("--completed", action="append", default=[])
    to.add_argument("--in-progress", action="append", default=[])
    to.add_argument("--next-action", action="append", default=[])

    inv = sub.add_parser("inventory", help="List or upsert tasks in the Barb task inventory.")
    inv.add_argument("--title", default="")
    inv.add_argument("--task-id", default="")
    inv.add_argument("--scope", default="barb:dispatch-coordinator")
    inv.add_argument("--status", default="")
    inv.add_argument("--surface", default="")
    inv.add_argument("--repo", default="")
    inv.add_argument("--target-ref", default="")

    ho = sub.add_parser("handoff", help="Transition CLI <-> Desktop handoff state with monotonic fencing epoch.")
    ho.add_argument("--task-id", required=True)
    ho.add_argument(
        "--action",
        required=True,
        choices=["request_desktop", "claim_desktop", "complete_desktop", "resume_cli", "complete_task"],
    )
    ho.add_argument("--actor", required=True)
    ho.add_argument("--persona", default="")
    ho.add_argument("--reason", default="")
    ho.add_argument("--desktop-app", default="")
    ho.add_argument("--artifact-uri", default="")
    ho.add_argument("--resume-notes", default="")
    ho.add_argument("--expected-epoch", type=int, required=True)

    for cmd_name, desc in (
        ("report-cards", "Fetch zero-LLM-cost Agent Report Cards & routing recommendations."),
        ("leaderboard", "Fetch empirical model/provider routing leaderboard."),
        ("bean-counter", "Fetch zero-LLM-cost Bean-Counter token economy & waste ledger."),
    ):
        p = sub.add_parser(cmd_name, help=desc)
        p.add_argument("--role", default="")
        p.add_argument("--provider", default="")
        p.add_argument("--since", default="")


def handle_ops_command(client: FleetClient, args: argparse.Namespace) -> int | None:
    cmd = args.command
    if cmd == "continuity":
        print(json.dumps(client.request("GET", "/api/v1/staff/barb/continuity"), indent=2))
        return 0
    if cmd == "heartbeat":
        res = client.request(
            "POST",
            "/api/v1/staff/barb/leases/heartbeat",
            body={
                "scope": args.scope,
                "epoch": args.epoch,
                "ttl_sec": args.ttl,
                **({"holder": args.holder} if args.holder else {}),
            },
        )
        print(json.dumps(res, indent=2))
        return 0
    if cmd == "renew":
        body: dict[str, Any] = {"scope": args.scope, "ttl_sec": args.ttl}
        for key, val in (
            ("holder", args.holder),
            ("persona", args.persona),
            ("machine", args.machine),
            ("summary", args.summary),
        ):
            if val:
                body[key] = val
        res = client.request("POST", "/api/v1/staff/barb/leases/renew", body=body)
        print(json.dumps(res, indent=2))
        return 0
    if cmd == "handoff-lease":
        res = client.request(
            "POST",
            "/api/v1/staff/barb/leases/handoff",
            body={
                "scope": args.scope,
                "persona": args.persona,
                "summary": args.summary,
                "ttl_sec": args.ttl,
                "completed_items": args.completed,
                "in_progress_items": getattr(args, "in_progress", []),
                "next_actions": getattr(args, "next_action", []),
                "blockers": getattr(args, "blocker", []),
                "takeover": bool(args.takeover),
                "reason": args.reason,
                **({"holder": args.holder} if args.holder else {}),
                **({"next_holder": args.next_holder} if args.next_holder else {}),
                **({"next_persona": args.next_persona} if args.next_persona else {}),
                **({"next_machine": args.next_machine} if args.next_machine else {}),
                **({"mode": args.mode} if args.mode else {}),
            },
        )
        print(json.dumps(res, indent=2))
        return 0
    if cmd == "checkout":
        res = client.request(
            "POST",
            "/api/v1/staff/barb/leases/checkout",
            body={
                "scope": args.scope,
                **({"holder": args.holder} if args.holder else {}),
                "persona": args.persona,
                "ttl_sec": args.ttl,
                "summary": args.summary,
                "takeover": bool(args.takeover),
                "reason": args.reason,
            },
        )
        print(json.dumps(res, indent=2))
        return 0
    if cmd == "release":
        res = client.request(
            "POST",
            "/api/v1/staff/barb/leases/release",
            body={
                "scope": args.scope,
                **({"holder": args.holder} if args.holder else {}),
                "turnover_summary": args.summary,
                "force": bool(args.force),
            },
        )
        print(json.dumps(res, indent=2))
        return 0
    if cmd == "turnover":
        if args.summary:
            res = client.request(
                "POST",
                "/api/v1/staff/barb/turnover",
                body={
                    "scope": args.scope,
                    "persona": args.persona,
                    **({"holder": args.holder} if args.holder else {}),
                    "summary": args.summary,
                    "completed_items": args.completed,
                    "in_progress_items": getattr(args, "in_progress", []),
                    "next_actions": getattr(args, "next_action", []),
                },
            )
        else:
            res = client.request("GET", "/api/v1/staff/barb/turnover", params={"scope": args.scope})
        print(json.dumps(res, indent=2))
        return 0
    if cmd == "inventory":
        if args.title:
            res = client.request(
                "POST",
                "/api/v1/staff/barb/inventory",
                body={
                    "task_id": args.task_id or None,
                    "title": args.title,
                    "scope": args.scope,
                    "status": args.status or "queued",
                    "surface": args.surface or None,
                    "repo": args.repo,
                    "target_ref": args.target_ref,
                },
            )
        else:
            params = {k: v for k, v in (("status", args.status), ("surface", args.surface)) if v}
            res = client.request("GET", "/api/v1/staff/barb/inventory", params=params)
        print(json.dumps(res, indent=2))
        return 0
    if cmd == "handoff":
        res = client.request(
            "POST",
            "/api/v1/staff/barb/handoff",
            body={
                "task_id": args.task_id,
                "action": args.action,
                "actor": args.actor,
                "persona": args.persona,
                "reason": args.reason,
                "desktop_app": args.desktop_app,
                "artifact_uri": args.artifact_uri,
                "resume_notes": args.resume_notes,
                "expected_epoch": args.expected_epoch,
            },
        )
        print(json.dumps(res, indent=2))
        return 0
    if cmd in ("report-cards", "leaderboard", "bean-counter"):
        params = {k: v for k, v in (("role", args.role), ("provider", args.provider), ("since", args.since)) if v}
        res = client.request("GET", f"/api/v1/staff/{cmd}", params=params)
        print(json.dumps(res, indent=2))
        return 0
    return None
