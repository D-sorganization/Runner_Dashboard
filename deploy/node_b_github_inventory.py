#!/usr/bin/env python3
"""Paginated GitHub and local metadata checks for qualified node-b deployment (#1838).

Contract
--------
Inputs are read-only: paginated GitHub runner list pages and optional local runner
install roots. Canonical GitHub names are ``d-sorg-local-node-b-{1..8}``. Legacy
standby metadata sometimes uses ``d-sorg-local-node-b-{N}``; that casing is
reported explicitly so operators re-register with the canonical name from
``docs/runbooks/runner-registration-purge-recovery.md`` instead of guessing.
Failure is fail-closed: missing slots, incomplete pagination, posture drift, or
legacy local ``agentName`` values block qualification.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CANONICAL_PREFIX = "d-sorg-local-node-b-"
LEGACY_PREFIX = "d-sorg-local-node-b-"
EXPECTED_SLOTS = tuple(range(1, 9))
PURGE_RUNBOOK = "docs/runbooks/runner-registration-purge-recovery.md"
_SLOT_PATTERN = re.compile(r"^d-sorg-local-O(?:GL|gl)aptop-([1-8])$")


@dataclass(frozen=True)
class InventoryFailure:
    message: str
    missing_slots: tuple[int, ...] = ()
    legacy_github_names: tuple[str, ...] = ()
    legacy_local_names: tuple[str, ...] = ()


class NodeBInventoryError(Exception):
    def __init__(self, failure: InventoryFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def _slot_from_name(name: str) -> int | None:
    match = _SLOT_PATTERN.match(name.strip())
    if not match:
        return None
    return int(match.group(1))


def canonical_name(slot: int) -> str:
    if slot not in EXPECTED_SLOTS:
        raise ValueError(f"slot out of range: {slot}")
    return f"{CANONICAL_PREFIX}{slot}"


def flatten_runner_pages(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int | None]:
    if not pages:
        return [], None
    runners = [runner for page in pages for runner in page.get("runners", [])]
    total_count = pages[0].get("total_count")
    return runners, total_count if isinstance(total_count, int) else None


def assert_pagination_complete(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runners, total_count = flatten_runner_pages(pages)
    if total_count is None:
        raise NodeBInventoryError(
            InventoryFailure("GitHub runner inventory pagination returned no total_count")
        )
    if total_count != len(runners):
        raise NodeBInventoryError(
            InventoryFailure("total_count proves the GitHub runner inventory is incomplete")
        )
    return runners


def classify_node-b_registrations(
    runners: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], tuple[str, ...]]:
    """Map slots 1-8 to GitHub runner rows; surface legacy-cased names separately."""
    canonical: dict[int, dict[str, Any]] = {}
    legacy_github: list[str] = []
    for runner in runners:
        name = str(runner.get("name", ""))
        slot = _slot_from_name(name)
        if slot is None:
            continue
        if name.startswith(LEGACY_PREFIX):
            legacy_github.append(name)
            continue
        if name != canonical_name(slot):
            continue
        if slot in canonical:
            raise NodeBInventoryError(
                InventoryFailure(f"duplicate canonical node-b registration for slot {slot}")
            )
        canonical[slot] = runner
    return canonical, tuple(sorted(legacy_github))


def _format_missing_message(
    *,
    missing_slots: tuple[int, ...],
    legacy_github: tuple[str, ...],
    legacy_local: tuple[str, ...],
    detail: str,
) -> str:
    parts = [detail]
    if missing_slots:
        slots = ", ".join(str(slot) for slot in missing_slots)
        parts.append(
            f"missing GitHub registrations for node-b slots [{slots}] "
            f"(standby purge recovery: {PURGE_RUNBOOK})"
        )
    if legacy_github:
        parts.append(
            "legacy-cased GitHub runner names present (re-register with canonical "
            f"{CANONICAL_PREFIX}{{N}}): "
            + ", ".join(legacy_github)
        )
    if legacy_local:
        parts.append(
            "local .runner agentName uses legacy casing (fix before re-registering): "
            + ", ".join(legacy_local)
        )
    return "; ".join(parts)


def assert_exact_canonical_inventory(
    pages: list[dict[str, Any]],
    *,
    legacy_local: tuple[str, ...] = (),
) -> dict[int, dict[str, Any]]:
    runners = assert_pagination_complete(pages)
    by_slot, legacy_github = classify_node-b_registrations(runners)
    missing = tuple(slot for slot in EXPECTED_SLOTS if slot not in by_slot)
    if missing or legacy_github or legacy_local:
        raise NodeBInventoryError(
            InventoryFailure(
                _format_missing_message(
                    missing_slots=missing,
                    legacy_github=legacy_github,
                    legacy_local=legacy_local,
                    detail="GitHub node-b inventory is not exactly runners 1 through 8",
                ),
                missing_slots=missing,
                legacy_github_names=legacy_github,
                legacy_local_names=legacy_local,
            )
        )
    return by_slot


def validate_qualified_pre_transaction(
    pages: list[dict[str, Any]],
    deployment_runner: str,
    *,
    legacy_local: tuple[str, ...] = (),
) -> None:
    by_slot = assert_exact_canonical_inventory(pages, legacy_local=legacy_local)
    for slot, runner in by_slot.items():
        if slot == 1:
            if runner["name"] != deployment_runner:
                raise NodeBInventoryError(
                    InventoryFailure("deployment runner name does not match the job runner")
                )
            if runner.get("status") != "online" or runner.get("busy") is not True:
                raise NodeBInventoryError(
                    InventoryFailure('the exact deployment runner must report "busy":true')
                )
        elif runner.get("busy") is not False:
            raise NodeBInventoryError(
                InventoryFailure("every other node-b runner must be idle")
            )
        expected_status = "online" if slot <= 4 else "offline"
        if runner.get("status") != expected_status:
            if slot <= 4:
                raise NodeBInventoryError(
                    InventoryFailure("daytime runners 1 through 4 must be online")
                )
            raise NodeBInventoryError(
                InventoryFailure("daytime runners 5 through 8 must be offline")
            )


def validate_qualified_post_transaction(
    pages: list[dict[str, Any]],
    deployment_runner: str,
    *,
    legacy_local: tuple[str, ...] = (),
) -> None:
    by_slot = assert_exact_canonical_inventory(pages, legacy_local=legacy_local)
    for slot, runner in by_slot.items():
        if slot == 1:
            if runner["name"] != deployment_runner or runner.get("busy") is not True:
                raise NodeBInventoryError(
                    InventoryFailure("deployment runner lost its exact-current-worker proof")
                )
        elif runner.get("busy") is not False:
            raise NodeBInventoryError(
                InventoryFailure("a non-deployment node-b runner became busy")
            )
        expected_status = "online" if slot <= 4 else "offline"
        if runner.get("status") != expected_status:
            raise NodeBInventoryError(
                InventoryFailure("post-transaction daytime capacity drifted")
            )


def evidence_summary(pages: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not pages:
        return {"available": False}
    runners, total_count = flatten_runner_pages(pages)
    by_slot, _legacy = classify_node-b_registrations(runners)
    prefix = CANONICAL_PREFIX
    return {
        "available": True,
        "complete": bool(pages) and total_count == len(runners),
        "online": sorted(slot for slot, row in by_slot.items() if row.get("status") == "online"),
        "busy": sorted(slot for slot, row in by_slot.items() if row.get("busy") is True),
        "missing_slots": sorted(slot for slot in EXPECTED_SLOTS if slot not in by_slot),
    }


def scan_local_legacy_agent_names(runner_root: Path) -> tuple[str, ...]:
    """Read-only scan for legacy ``node-b`` casing in local ``.runner`` metadata."""
    if not runner_root.is_dir():
        return ()
    legacy: list[str] = []
    for runner_dir in sorted(runner_root.glob("runner-*")):
        if not runner_dir.is_dir():
            continue
        runner_file = runner_dir / ".runner"
        if not runner_file.is_file():
            continue
        try:
            payload = json.loads(runner_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        agent_name = payload.get("agentName")
        if not isinstance(agent_name, str):
            continue
        if agent_name.startswith(LEGACY_PREFIX):
            legacy.append(agent_name)
    return tuple(sorted(set(legacy)))


def _load_pages(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("runner pages file must contain a JSON array")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("validate-pre", help="Pre-transaction qualified GitHub inventory")
    pre.add_argument("pages_file", type=Path)
    pre.add_argument("deployment_runner")

    post = sub.add_parser("validate-post", help="Post-transaction steady-state inventory")
    post.add_argument("pages_file", type=Path)
    post.add_argument("deployment_runner")

    summary = sub.add_parser("evidence-summary", help="Redacted workflow evidence fragment")
    summary.add_argument("pages_file", type=Path)
    summary.add_argument("output_file", type=Path)

    scan = sub.add_parser("scan-local", help="Detect legacy local agentName casing")
    scan.add_argument("runner_root", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "scan-local":
            names = scan_local_legacy_agent_names(args.runner_root)
            if names:
                failure = InventoryFailure(
                    _format_missing_message(
                        missing_slots=(),
                        legacy_github=(),
                        legacy_local=names,
                        detail="local node-b runner metadata uses non-canonical names",
                    ),
                    legacy_local_names=names,
                )
                raise NodeBInventoryError(failure)
            return 0

        pages = _load_pages(args.pages_file)
        runner_root = Path(os.environ.get("QUALIFIED_RUNNER_ROOT", "/home/fleet-operator/actions-runners"))
        if args.command == "validate-pre":
            legacy_local = scan_local_legacy_agent_names(runner_root)
            validate_qualified_pre_transaction(
                pages, args.deployment_runner, legacy_local=legacy_local
            )
        elif args.command == "validate-post":
            legacy_local = scan_local_legacy_agent_names(runner_root)
            validate_qualified_post_transaction(
                pages, args.deployment_runner, legacy_local=legacy_local
            )
        elif args.command == "evidence-summary":
            fragment = evidence_summary(pages)
            args.output_file.write_text(
                json.dumps(fragment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return 0
    except NodeBInventoryError as exc:
        print(exc.failure.message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
