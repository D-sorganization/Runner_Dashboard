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
    ARMED_PULL_REQUESTS_PAGE_QUERY,
    OPEN_PRS_QUERY,
    PassConfig,
    armed_pull_requests_has_next_page,
    holds_gate_failed_payload,
    resolve_hub_holds_policy,
    resolve_live_arming,
    run_merge_shepherd_pass,
    stitch_armed_pull_request_pages,
    summary_to_json,
)

log = logging.getLogger("merge_shepherd_pass")


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def _graphql_query(
    query: str,
    owner: str,
    name: str,
    *,
    after: str | None = None,
    runner=_default_runner,
) -> Mapping[str, Any]:
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
    ]
    if after:
        cmd.extend(["-f", f"after={after}"])
    proc = runner(cmd)
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or proc.stdout.strip() or "graphql failed")
    data = json.loads(proc.stdout or "{}")
    return data.get("data") if isinstance(data, dict) else {}


def _graphql(owner: str, name: str, *, runner=_default_runner) -> Mapping[str, Any]:
    data = _graphql_query(OPEN_PRS_QUERY, owner, name, runner=runner)
    if not isinstance(data, Mapping):
        return {}
    repo = data.get("repository")
    if not isinstance(repo, Mapping):
        return data
    armed_block = repo.get("armedPullRequests")
    if not isinstance(armed_block, Mapping):
        return data
    pages: list[Mapping[str, Any]] = [armed_block]
    has_next, cursor = armed_pull_requests_has_next_page(armed_block)
    while has_next:
        if not cursor:
            raise OSError("armed_pull_requests_pagination_incomplete:missing_end_cursor")
        page_data = _graphql_query(
            ARMED_PULL_REQUESTS_PAGE_QUERY,
            owner,
            name,
            after=cursor,
            runner=runner,
        )
        if not isinstance(page_data, Mapping):
            raise OSError("armed_pull_requests_pagination_incomplete:invalid_page_payload")
        next_repo = page_data.get("repository")
        if not isinstance(next_repo, Mapping):
            raise OSError("armed_pull_requests_pagination_incomplete:missing_repository")
        next_block = next_repo.get("armedPullRequests")
        if not isinstance(next_block, Mapping):
            raise OSError("armed_pull_requests_pagination_incomplete:missing_armed_pull_requests")
        pages.append(next_block)
        has_next, cursor = armed_pull_requests_has_next_page(next_block)
    stitched = stitch_armed_pull_request_pages(pages)
    merged_repo = dict(repo)
    merged_repo["armedPullRequests"] = stitched
    return {**data, "repository": merged_repo}


def _fetch_hub_holds(url: str, token: str) -> list[dict[str, Any]]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{url}/api/v1/staff/holds",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            code = resp.getcode()
            if code and code >= 400:
                raise OSError(f"http_{code}")
            raw = resp.read().decode("utf-8")
            body = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise OSError(f"http_{exc.code}") from exc
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        raise OSError(f"unparseable:{exc}") from exc
    if not isinstance(body, dict):
        raise OSError("unparseable:not_object")
    holds = body.get("holds")
    if holds is None:
        raise OSError("unparseable:missing_holds_key")
    if not isinstance(holds, list):
        raise OSError("unparseable:holds_not_list")
    rows: list[dict[str, Any]] = []
    for h in holds:
        if not isinstance(h, dict):
            raise OSError("unparseable:hold_row_not_object")
        rows.append(dict(h))
    return rows


def hub_fleet_token_for_holds() -> str:
    """Hub ``GET /api/v1/staff/holds`` accepts the fleet bearer, not a GitHub App token."""
    return os.environ.get("HUB_FLEET_TOKEN", "").strip()


def _resolve_hub_holds() -> tuple[list[dict[str, Any]], str | None]:
    token = hub_fleet_token_for_holds()
    return resolve_hub_holds_policy(
        mode=os.environ.get("MERGE_SHEPHERD_HUB_HOLDS", "required"),
        dashboard_url=os.environ.get("RUNNER_DASHBOARD_URL", ""),
        token=token,
        fetch_holds=_fetch_hub_holds,
    )


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
    live = resolve_live_arming(
        merge_shepherd_live=os.environ.get("MERGE_SHEPHERD_LIVE", ""),
        input_dry_run=os.environ.get("INPUT_DRY_RUN", ""),
        cli_dry_run=bool(args.dry_run),
        github_event_name=os.environ.get("GITHUB_EVENT_NAME", ""),
    )

    config = PassConfig(
        live=live,
        dry_run=not live,
        repo_root=args.repo_root.resolve(),
    )
    holds, hold_error = _resolve_hub_holds()
    if hold_error:
        payload = holds_gate_failed_payload(live=live, reason=hold_error)
        _write_step_summary(payload)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
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
