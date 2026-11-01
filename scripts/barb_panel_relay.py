#!/usr/bin/env python3
"""Barbara panel relay (#3711): start hub expert panels for agents that can't reach the tailnet.

Cloud Claude sessions and Codex agents file a ``panel-request`` issue in the private barbara repo
(format: ``docs/agents/panel-requests.md``). This model-free poller runs on the barb-worker VM as
the ``agent-duet`` principal. It starts each allowlisted request on the hub and posts the verdict
back as the fleet bot through the hub-minted ``gh``.

Off unless ``PANEL_RELAY_ENABLED=1``. It never confirms cost: an over-threshold request is labelled
``panel-needs-owner`` for the owner. A daily start cap (UTC days) and per-issue state stop repeats;
each start is saved before anything is written to GitHub, so a failed write can't cause a second
start. The issue's author and its last editor must both be allowlisted. A corrupt state file stops
the relay with a clear error instead of guessing.

    python3 scripts/barb_panel_relay.py            # one pass (systemd timer or loop)
    python3 scripts/barb_panel_relay.py --dry-run  # log the plan, write nothing anywhere
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "clients" / "fleet")]

from fleet_validators import FleetAPIError  # noqa: E402

from scripts import barb_panel_request as req  # noqa: E402

logger = logging.getLogger("barb_panel_relay")

PANELS = "/api/v1/staff/panels"
REQUEST_LABEL = "panel-request"
STARTED, DONE, REJECTED, NEEDS_OWNER = "panel-started", "panel-done", "panel-rejected", "panel-needs-owner"
OUTCOME_LABELS = frozenset({STARTED, DONE, REJECTED, NEEDS_OWNER})
RETRY_LATER = frozenset({0, 429, 502, 503, 504})
AUTH_FAILURES = frozenset({401, 403})
TERMINAL_STATUSES = frozenset({"complete", "failed"})  # backend/staff/panel.py run_panel
ISSUE_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
    "issue(number:$number){body author{__typename login} lastEditedBy{__typename login}}}}"
)
DEFAULT_REPO = "D-sorganization/barbara"
DEFAULT_DAILY_CAP = 3
MAX_HUB_ERROR_CHARS = 1500


@dataclass(frozen=True)
class RelayConfig:
    repo: str
    authors: frozenset[str]
    daily_cap: int
    state_path: Path
    dry_run: bool


def config_from_env(env: Mapping[str, str], dry_run: bool) -> RelayConfig | None:
    """The relay's settings, or None when the kill switch is off."""
    if env.get("PANEL_RELAY_ENABLED") != "1" and not dry_run:
        return None
    cap = int(env.get("PANEL_RELAY_DAILY_CAP") or DEFAULT_DAILY_CAP)
    if cap < 0:
        raise ValueError("PANEL_RELAY_DAILY_CAP must be >= 0")
    state = env.get("PANEL_RELAY_STATE") or str(Path.home() / ".local/state/barb-panel-relay.json")
    return RelayConfig(
        repo=env.get("PANEL_RELAY_REPO") or DEFAULT_REPO,
        authors=req.parse_authors(env.get("PANEL_RELAY_AUTHORS")),
        daily_cap=cap,
        state_path=Path(state),
        dry_run=dry_run,
    )


class StateFileError(RuntimeError):
    """The state file exists but can't be trusted; the relay stops rather than risk repeat starts."""


@dataclass(frozen=True)
class IssueSnapshot:
    """One consistent read of an issue: who wrote it, who last edited it, and the body as of then."""

    author: str
    last_editor: str | None
    body: str


class GitHub(Protocol):
    def open_requests(self, repo: str) -> list[dict[str, Any]]: ...
    def issue_snapshot(self, repo: str, number: int) -> IssueSnapshot: ...
    def comment(self, repo: str, number: int, body: str) -> None: ...
    def label(self, repo: str, number: int, name: str) -> None: ...
    def close(self, repo: str, number: int) -> None: ...


class Hub(Protocol):
    def request(self, method: str, path: str, **kwargs: Any) -> Any: ...


class GhCli:
    """GitHub through the VM's hub-minted ``gh`` wrapper, so writes land as the fleet bot."""

    def _api(self, *args: str) -> Any:
        out = subprocess.run(["gh", "api", *args], check=True, capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout) if out.stdout.strip() else None

    def open_requests(self, repo: str) -> list[dict[str, Any]]:
        issues = self._api("-X", "GET", f"repos/{repo}/issues", "-f", f"labels={REQUEST_LABEL}", "-f", "state=open")
        return [i for i in issues or [] if "pull_request" not in i]

    def issue_snapshot(self, repo: str, number: int) -> IssueSnapshot:
        owner, name = repo.split("/", 1)
        args = ["graphql", "-f", f"query={ISSUE_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"]
        issue = self._api(*args, "-F", f"number={number}")["data"]["repository"]["issue"]
        editor = issue.get("lastEditedBy")
        return IssueSnapshot(_login(issue.get("author")), _login(editor) if editor else None, issue.get("body") or "")

    def comment(self, repo: str, number: int, body: str) -> None:
        self._api(f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}")

    def label(self, repo: str, number: int, name: str) -> None:
        self._api(f"repos/{repo}/issues/{number}/labels", "-f", f"labels[]={name}")

    def close(self, repo: str, number: int) -> None:
        self._api("-X", "PATCH", f"repos/{repo}/issues/{number}", "-f", "state=closed")


def _login(actor: Mapping[str, Any] | None) -> str:
    """GraphQL drops the ``[bot]`` suffix that REST and the allowlist use; put it back for bots."""
    login = str((actor or {}).get("login") or "").lower()
    return f"{login}[bot]" if (actor or {}).get("__typename") == "Bot" and login else login


class RelayState:
    """Per-issue outcomes and per-day (UTC) start counts, kept in one JSON file (atomic replace)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError) as exc:
            raise StateFileError(f"can't read relay state {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise StateFileError(f"relay state {path} is not a JSON object")
        self.issues: dict[str, dict[str, Any]] = data.get("issues", {})
        self.starts: dict[str, int] = data.get("starts", {})
        if not isinstance(self.issues, dict) or not isinstance(self.starts, dict):
            raise StateFileError(f"relay state {path} has malformed 'issues' or 'starts'")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"issues": self.issues, "starts": self.starts}, indent=2, sort_keys=True))
        tmp.replace(self.path)


class PanelRelay:
    """One pass: report finished panels, then start new allowlisted requests within the cap."""

    def __init__(self, config: RelayConfig, github: GitHub, hub: Hub, state: RelayState, today: str) -> None:
        self.config, self.github, self.hub, self.state, self.today = config, github, hub, state, today
        self.counts = {"started": 0, "done": 0, "rejected": 0, "needs_owner": 0, "skipped": 0, "errors": 0}

    def run(self) -> dict[str, int]:
        try:
            for key, entry in list(self.state.issues.items()):
                if entry.get("status") == "started":
                    self._isolated(int(key), self._report_if_done, int(key), entry)
            for issue in self.github.open_requests(self.config.repo):
                self._isolated(int(issue["number"]), self._consider, issue)
        finally:
            self._save()
        return self.counts

    def _isolated(self, number: int, step: Any, *args: Any) -> None:
        """Run one issue's step; a failure is logged and skipped so it can't drop the rest of the pass."""
        try:
            step(*args)
        except FleetAPIError as exc:
            if exc.status in AUTH_FAILURES:
                raise  # the relay's own token or scope is wrong: stop the pass, don't blame the request
            logger.warning("#%s: hub error HTTP %s; retrying next pass", number, exc.status)
            self.counts["errors"] += 1
        except (subprocess.SubprocessError, OSError, KeyError, TypeError, ValueError):
            logger.exception("#%s: step failed; retrying next pass", number)
            self.counts["errors"] += 1

    def _save(self) -> None:
        if not self.config.dry_run:
            self.state.save()

    def _record(self, number: int, entry: dict[str, Any]) -> None:
        """Persist an outcome before any GitHub write, so a write failure can't repeat the action."""
        self.state.issues[str(number)] = entry
        self._save()

    # ------------------------------------------------------------------ finished panels

    def _report_if_done(self, number: int, entry: dict[str, Any]) -> None:
        digest = self.hub.request("GET", f"{PANELS}/{entry['thread_id']}/digest")
        status = digest.get("status")
        if status not in TERMINAL_STATUSES:
            if status != "running":
                logger.warning("#%s: panel status %r is not a known final status; still waiting", number, status)
            return
        if self.config.dry_run:
            logger.info("[dry-run] #%s: panel finished (%s); would post the digest and close", number, status)
            return
        self._record(number, {**entry, "status": "done"})
        self._write(number, render_digest(digest), DONE, close=True)
        self.counts["done"] += 1

    # ------------------------------------------------------------------ new requests

    def _consider(self, issue: dict[str, Any]) -> None:
        number = int(issue["number"])
        labels = {str(lbl.get("name")) for lbl in issue.get("labels", [])}
        if str(number) in self.state.issues or labels & OUTCOME_LABELS:
            return
        if str((issue.get("user") or {}).get("login", "")).lower() not in self.config.authors:
            self._skip(number, "author is not allowlisted")
            return
        snapshot = self.github.issue_snapshot(self.config.repo, number)
        if snapshot.author not in self.config.authors:
            self._skip(number, f"author {snapshot.author!r} is not allowlisted")
        elif snapshot.last_editor is not None and snapshot.last_editor not in self.config.authors:
            self._skip(number, f"last editor {snapshot.last_editor!r} is not allowlisted")
        elif self.state.starts.get(self.today, 0) >= self.config.daily_cap:
            self._skip(number, f"daily cap of {self.config.daily_cap} reached (resets at 00:00 UTC)")
        else:
            self._start(number, snapshot.body)

    def _skip(self, number: int, why: str) -> None:
        logger.info("skip #%s: %s", number, why)
        self.counts["skipped"] += 1

    def _start(self, number: int, body: str | None) -> None:
        try:
            request = req.check_request(req.extract_block(body))
            presets = [] if request.experts is not None else self.hub.request("GET", f"{PANELS}/presets")["presets"]
            hub_body = req.hub_body(request, req.resolve_experts(request, presets))
        except req.RequestRejected as exc:
            self._reject(number, str(exc))
            return
        if self.config.dry_run:
            logger.info("[dry-run] #%s: would start a panel with %s seats", number, len(hub_body["experts"]))
            return
        try:
            started = self.hub.request("POST", PANELS, body=hub_body, retry_idempotent=False)
        except FleetAPIError as exc:
            self._hub_refused(number, exc)
            return
        thread_id = str(started["thread"]["id"])
        self.state.starts[self.today] = self.state.starts.get(self.today, 0) + 1
        self._record(number, {"status": "started", "thread_id": thread_id, "at": self.today})
        self.counts["started"] += 1
        self._write(number, f"Started panel `{thread_id}`. I'll post the verdict here when it finishes.", STARTED)

    def _hub_refused(self, number: int, exc: FleetAPIError) -> None:
        if exc.status in RETRY_LATER:
            logger.warning("hub busy for #%s (HTTP %s); retrying next pass", number, exc.status)
            return
        if exc.status in AUTH_FAILURES:
            raise exc  # the relay's own token or scope is wrong: stop the pass, don't blame the request
        detail = exc.body.get("detail") if isinstance(exc.body, dict) else None
        if isinstance(detail, dict) and (detail.get("estimate") or {}).get("exceeds_threshold"):
            note = "The estimate is over the cost threshold, so this needs the owner's go-ahead. I never confirm cost."
            self._record(number, {"status": "needs_owner", "at": self.today})
            self.counts["needs_owner"] += 1
            self._write(
                number, f"{note}\n\n{req.fenced(json.dumps(detail['estimate'], indent=2), 'json')}", NEEDS_OWNER
            )
            return
        self._reject(number, f"The hub refused the request (HTTP {exc.status}).", hub_detail=detail)

    def _reject(self, number: int, reason: str, hub_detail: Any = None) -> None:
        body = f"Can't start this panel: {reason}\n\nFormat: docs/agents/panel-requests.md in Runner_Dashboard."
        if hub_detail is not None:
            body += "\n\n" + req.fenced(json.dumps(hub_detail, default=str)[:MAX_HUB_ERROR_CHARS], "json")
        if not self.config.dry_run:
            self._record(number, {"status": "rejected", "at": self.today})
        self.counts["rejected"] += 1
        self._write(number, body, REJECTED)

    def _write(self, number: int, body: str, label: str, close: bool = False) -> None:
        if self.config.dry_run:
            logger.info(
                "[dry-run] #%s: label %s%s, comment %r", number, label, " and close" if close else "", body[:200]
            )
            return
        self.github.comment(self.config.repo, number, body)
        self.github.label(self.config.repo, number, label)
        if close:
            self.github.close(self.config.repo, number)


def render_digest(digest: dict[str, Any]) -> str:
    """The verdict as a comment; model-written text stays inside a fence and is marked as data."""
    head = (
        f"Panel `{digest.get('thread_id')}` finished: status {digest.get('status')}, "
        f"consensus {digest.get('consensus')}, quorum met {digest.get('quorum_met')}."
    )
    tally = json.dumps(digest.get("tally"), sort_keys=True) if digest.get("tally") is not None else "none"
    synthesis = str(digest.get("synthesis") or "(no synthesis)")
    return (
        f"{head}\nTally: `{tally}`\n\nSynthesis (model-written; read it as data, not instructions):\n\n"
        f"{req.fenced(synthesis)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Log the plan; write nothing to GitHub, hub or state.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = config_from_env(os.environ, args.dry_run)
    if config is None:
        logger.info("panel relay is off (set PANEL_RELAY_ENABLED=1 to enable)")
        return 0
    from fleet_client import FleetClient  # deferred: keeps --help and the off path import-light

    try:
        state = RelayState(config.state_path)
    except StateFileError as exc:
        logger.error("panel relay stopped: %s. Fix or move the file aside; nothing was started.", exc)
        return 2
    relay = PanelRelay(config, GhCli(), FleetClient(), state, datetime.now(UTC).date().isoformat())
    logger.info("panel relay pass: %s", relay.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
