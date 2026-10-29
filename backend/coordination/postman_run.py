"""Dry-run-by-default entry point for the postman engine (issue #2940).

``python -m coordination.postman_run`` plans one cycle against the real board and agent bus and logs
the plan without writing anything. ``--live`` applies it: presence is renewed, deliveries reach the
agent bus, and the state file is saved. Run one supervised dry run and have it reviewed before the
first ``--live`` run against production (see issue #2940); this module only makes that switch safe,
it does not perform that review.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from coordination.hub_thread_bus import build_hub_thread_bus
from coordination.postman import BARB, CycleReport, Postman, PostmanState
from coordination.postman_adapters import ConfigBoardSource, RmBoard
from coordination.roster import agent_ids

log = logging.getLogger("dashboard.coordination.postman_run")

DEFAULT_STATE_PATH = Path("data") / "postman_state.json"
#: Roster entries that are not addressable bots (so postman should not keep presence for them).
NON_AGENT_ROSTER_ENTRIES = frozenset({"user"})


def default_roster() -> tuple[str, ...]:
    return tuple(a for a in agent_ids() if a not in NON_AGENT_ROSTER_ENTRIES)


def _report_dict(report: CycleReport) -> dict[str, object]:
    return {
        "dry_run": report.dry_run,
        "board_issue": report.board_issue,
        "actions": [{"kind": a.kind, "key": a.key, "detail": a.detail} for a in report.actions],
    }


def build_postman(state_path: Path, *, roster: tuple[str, ...] | None = None) -> Postman:
    source = ConfigBoardSource()
    agents = roster or default_roster()
    state = PostmanState.load(state_path, default_board_issue=source.board_issue())
    bus = build_hub_thread_bus(roster=frozenset(agents) | {BARB})
    return Postman(RmBoard(), bus, source, agents, state, guard=bus.guard)


def run(pm: Postman, state_path: Path, *, live: bool) -> CycleReport:
    """Plan (and, when ``live``, apply) one cycle. Post: the state file is only touched when ``live``."""
    report = pm.cycle(dry_run=not live)
    if live:
        pm.state.save(state_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="apply the cycle; default is a dry run")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="state file path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    pm = build_postman(args.state)
    report = run(pm, args.state, live=args.live)
    log.info("postman %s: %s", "live" if args.live else "dry-run", json.dumps(_report_dict(report)))
    if any(a.kind == "error" for a in report.actions):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
