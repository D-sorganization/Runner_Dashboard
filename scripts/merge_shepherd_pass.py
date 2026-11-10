#!/usr/bin/env python3
"""Scheduled merge shepherd: one GraphQL pass per fleet repo, capped auto-arm."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from coordination.merge_shepherd_pass import (  # noqa: E402
    OPEN_PRS_QUERY,
    PassConfig,
    run_merge_shepherd_pass,
    summary_to_json,
)

log = logging.getLogger("merge_shepherd_pass")


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def _graphql(owner: str, name: str, *, runner=_default_runner) -> Mapping[str, Any]:
    proc = runner(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={OPEN_PRS_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
        ]
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or proc.stdout.strip() or "graphql failed")
    data = json.loads(proc.stdout or "{}")
    return data.get("data") if isinstance(data, dict) else {}


def _load_hub_holds() -> list[dict[str, Any]]:
    url = os.environ.get("RUNNER_DASHBOARD_URL", "").strip().rstrip("/")
    token = os.environ.get("HUB_FLEET_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    if not url or not token:
        return []
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{url}/api/v1/staff/holds",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        log.warning("hub holds unavailable: %s", exc)
        return []
    holds = body.get("holds") if isinstance(body, dict) else None
    return [dict(h) for h in holds] if isinstance(holds, list) else []


def _arm_via_guard(repo: str, pr: int, *, strategy: str, head_sha: str, repo_root: Path) -> dict[str, Any]:
    _SCRIPT_DIR = str(Path(__file__).resolve().parent)
    if _SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _SCRIPT_DIR)
    import automerge_guard  # noqa: PLC0415

    argv = [
        repo,
        str(pr),
        "--arm",
        f"--strategy={strategy}",
        "--require-app-token",
        "--repo-root",
        str(repo_root),
    ]
    if head_sha:
        argv.extend(["--match-head-commit", head_sha])
    code = automerge_guard.main(argv)
    return {"exit_code": code, "armed": code == 0}


def _write_step_summary(payload: dict[str, Any]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("## Merge shepherd pass\n\n")
        fh.write("```json\n")
        fh.write(json.dumps(payload, indent=2, sort_keys=True))
        fh.write("\n```\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true", help="force dry run (no arming)")
    parser.add_argument("--json", action="store_true", help="print JSON summary to stdout")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    live = os.environ.get("MERGE_SHEPHERD_LIVE", "").strip().lower() == "true"
    if args.dry_run or os.environ.get("INPUT_DRY_RUN", "").strip().lower() in {"true", "1", "yes"}:
        live = False

    config = PassConfig(
        live=live,
        dry_run=not live,
        repo_root=args.repo_root.resolve(),
    )
    holds = _load_hub_holds()
    summary = run_merge_shepherd_pass(
        config=config,
        fetch_graphql=lambda o, n: _graphql(o, n),
        holds=holds,
    )
    executed: list[dict[str, Any]] = []
    if live:
        for row in summary.armed:
            if row.get("dry_run"):
                continue
            result = _arm_via_guard(
                str(row["repo"]),
                int(row["pr"]),
                strategy=str(row.get("strategy") or "squash"),
                head_sha=str(row.get("head_sha") or ""),
                repo_root=config.repo_root,
            )
            executed.append({"repo": row["repo"], "pr": row["pr"], **result})
    payload = summary_to_json(summary)
    payload["live"] = live
    payload["executed"] = executed
    _write_step_summary(payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
