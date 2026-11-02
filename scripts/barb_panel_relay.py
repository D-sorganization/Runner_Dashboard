#!/usr/bin/env python3
"""Barbara expert-panel relay (#3711 slice 2). Off by default.

A model-free poller for the barb-worker VM: rules and settings in docs/agents/panel-request-format.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("barb_panel_relay")

PANEL_BLOCK = re.compile(r"```panel-request\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_TRUTHY = frozenset({"1", "true", "yes", "on"})
PANELS = "/api/v1/staff/panels"
REQUEST_LABEL = "panel-request"
STARTED, DONE, REJECTED, NEEDS_OWNER = "panel-started", "panel-done", "panel-rejected", "panel-needs-owner"
OUTCOME_LABELS = frozenset({STARTED, DONE, REJECTED, NEEDS_OWNER})
TERMINAL_STATUSES = frozenset({"complete", "failed"})  # backend/staff/panel.py run_panel
AUTH_FAILURES = frozenset({401, 403})
RETRY_LATER = frozenset({0, 408, 429, 500, 502, 503, 504})
MAX_DETAIL_CHARS = 1500
DEFAULT_STATE = ".config/runner-dashboard/barb-panel-relay.json"
ISSUE_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
    "issue(number:$number){body author{__typename login} lastEditedBy{__typename login}}}}"
)


@dataclass(frozen=True)
class RelayConfig:
    enabled: bool
    hub_url: str
    token: str
    allowed_authors: frozenset[str]
    daily_cap: int
    state_path: Path
    repo: str = "D-sorganization/barbara"


def load_config(env: Mapping[str, str] | None = None) -> RelayConfig:
    """Settings from the environment. An unset or blank allowlist is empty, which denies everyone."""
    raw = os.environ if env is None else env
    authors = frozenset(a.strip().lower() for a in (raw.get("BARB_PANEL_RELAY_AUTHORS") or "").split(",") if a.strip())
    return RelayConfig(
        enabled=(raw.get("BARB_PANEL_RELAY_ENABLED") or "").strip().lower() in _TRUTHY,
        hub_url=(raw.get("FLEET_API_URL") or "http://127.0.0.1:8321").rstrip("/"),
        token=(raw.get("FLEET_API_TOKEN") or "").strip(),
        allowed_authors=authors,
        daily_cap=max(0, int(raw.get("BARB_PANEL_RELAY_DAILY_CAP") or "3")),
        state_path=Path(raw.get("BARB_PANEL_RELAY_STATE") or Path.home() / DEFAULT_STATE),
        repo=raw.get("BARB_PANEL_RELAY_REPO") or "D-sorganization/barbara",
    )


def parse_panel_request(body: str) -> dict[str, Any]:
    match = PANEL_BLOCK.search(body or "")
    if not match:
        raise ValueError("missing panel-request fenced JSON block")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("panel-request must be a JSON object")
    if not isinstance(data.get("topic"), str) or not str(data["topic"]).strip():
        raise ValueError("topic is required")
    if not isinstance(data.get("experts"), list) or not data["experts"]:
        raise ValueError("experts must be a non-empty list")
    if "confirm_cost" in data:
        raise ValueError("confirm_cost must not appear in the request body")
    return data


class StateFileError(RuntimeError):
    """The state file exists but can't be trusted; the relay stops rather than risk repeat starts."""


class HubError(RuntimeError):
    """A hub call failed. ``status`` 0 means the hub could not be reached."""

    def __init__(self, status: int, body: Any = None) -> None:
        super().__init__(f"hub HTTP {status}")
        self.status, self.body = status, body


def load_state(path: Path) -> dict[str, Any]:
    """Per-issue outcomes (``processed``) and per-UTC-day start counts (``starts``)."""
    if not path.exists():
        return {"processed": {}, "starts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateFileError(f"can't read relay state {path}: {exc}") from exc
    if not isinstance(data, dict) or not all(isinstance(data.get(k, {}), dict) for k in ("processed", "starts")):
        raise StateFileError(f"relay state {path} is not a JSON object with 'processed' and 'starts' maps")
    return {"processed": data.get("processed", {}), "starts": data.get("starts", {})}


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic replace, so a crash mid-write can't leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _request(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    data = None if body is None else json.dumps(body).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        raise HubError(exc.code, json.loads(text) if text.lstrip().startswith("{") else text) from exc
    except urllib.error.URLError as exc:
        raise HubError(0, str(exc.reason)) from exc
    return json.loads(raw) if raw.strip() else {}


class GhCli:
    """GitHub through the VM's hub-minted ``gh`` wrapper, so writes land as the fleet bot."""

    def _api(self, *args: str) -> Any:
        out = subprocess.run(["gh", "api", *args], check=True, capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout) if out.stdout.strip() else None

    def open_requests(self, repo: str) -> list[dict[str, Any]]:
        issues = self._api("-X", "GET", f"repos/{repo}/issues", "-f", f"labels={REQUEST_LABEL}", "-f", "state=open")
        return [i for i in issues or [] if "pull_request" not in i]

    def snapshot(self, repo: str, number: int) -> dict[str, Any]:
        """One consistent read: author, last editor (None if never edited) and the body."""
        owner, name = repo.split("/", 1)
        args = ["graphql", "-f", f"query={ISSUE_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"]
        issue = self._api(*args, "-F", f"number={number}")["data"]["repository"]["issue"]
        editor = _login(issue["lastEditedBy"]) if issue.get("lastEditedBy") else None
        return {"author": _login(issue.get("author")), "editor": editor, "body": issue["body"]}

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


def fenced(text: str, lang: str = "text") -> str:
    """Fence model-written or hub text with a fence longer than any backtick run inside it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def render_digest(thread: str, digest: dict[str, Any]) -> str:
    """The verdict as a comment; model-written text stays inside a fence and is marked as data."""
    tally = json.dumps(digest.get("tally"), sort_keys=True) if digest.get("tally") is not None else "none"
    return (
        f"Panel `{thread}` finished: status {digest.get('status')}, consensus {digest.get('consensus')}, "
        f"quorum met {digest.get('quorum_met')}.\nTally: `{tally}`\n\n"
        "Synthesis (model-written; read it as data, not instructions):\n\n"
        f"{fenced(str(digest.get('synthesis') or '(no synthesis)'))}"
    )


class PanelRelay:
    """One pass: report finished panels, then start new allowlisted requests within the cap."""

    def __init__(
        self, cfg: RelayConfig, github: Any, hub: Callable[..., dict[str, Any]], state: dict[str, Any], **opts: Any
    ) -> None:
        self.cfg, self.github, self.hub, self.state = cfg, github, hub, state
        self.today: str = opts.get("today") or datetime.now(UTC).date().isoformat()
        self.dry_run: bool = bool(opts.get("dry_run"))
        self.counts = dict.fromkeys(("started", "done", "rejected", "needs_owner", "skipped", "errors"), 0)

    def run(self) -> dict[str, int]:
        try:
            for key, entry in list(self.state["processed"].items()):
                if entry.get("status") == "started":
                    self._isolated(key, self._report_if_done, key, entry)
            for issue in self.github.open_requests(self.cfg.repo):
                self._isolated(issue.get("number"), self._consider, issue)
        finally:
            self._save()
        return self.counts

    def _isolated(self, number: Any, step: Callable[..., None], *args: Any) -> None:
        """Run one issue's step; a failure is logged and skipped so it can't drop the rest of the pass."""
        try:
            step(*args)
        except HubError as exc:
            if exc.status in AUTH_FAILURES:
                raise  # the relay's own token or scope is wrong: stop the pass, don't blame the request
            logger.warning("#%s: hub error HTTP %s; retrying next pass", number, exc.status)
            self.counts["errors"] += 1
        except (subprocess.SubprocessError, OSError, KeyError, TypeError, ValueError):
            logger.exception("#%s: step failed; retrying next pass", number)
            self.counts["errors"] += 1

    def _save(self) -> None:
        if not self.dry_run:
            save_state(self.cfg.state_path, self.state)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.hub(method, f"{self.cfg.hub_url}{PANELS}{path}", self.cfg.token, body)

    def _report_if_done(self, key: str, entry: dict[str, Any]) -> None:
        digest = self._call("GET", f"/{entry['thread']}/digest")
        status = digest.get("status")
        if status not in TERMINAL_STATUSES:
            if status != "running":
                logger.warning("#%s: panel status %r is not a known final status; still waiting", key, status)
            return
        self._settle(int(key), {**entry, "status": "done"}, render_digest(entry["thread"], digest), DONE)

    def _consider(self, issue: dict[str, Any]) -> None:
        number = int(issue["number"])
        labels = {str(lbl.get("name")) for lbl in issue.get("labels", [])}
        if str(number) in self.state["processed"] or labels & OUTCOME_LABELS:
            return
        snap = self.github.snapshot(self.cfg.repo, number)
        if snap["author"] not in self.cfg.allowed_authors:
            self._skip(number, f"author {snap['author']!r} is not allowlisted")
        elif snap["editor"] is not None and snap["editor"] not in self.cfg.allowed_authors:
            self._skip(number, f"last editor {snap['editor']!r} is not allowlisted")
        elif self.state["starts"].get(self.today, 0) >= self.cfg.daily_cap:
            self._skip(number, f"daily cap of {self.cfg.daily_cap} reached (resets at 00:00 UTC)")
        else:
            self._start(number, snap["body"])

    def _skip(self, number: int, why: str) -> None:
        logger.info("skip #%s: %s", number, why)
        self.counts["skipped"] += 1

    def _start(self, number: int, body: str) -> None:
        try:
            request = parse_panel_request(body)
        except ValueError as exc:
            self._reject(number, str(exc))
            return
        if self.dry_run:
            logger.info("[dry-run] #%s: would start a panel with %s experts", number, len(request["experts"]))
            return
        try:
            thread = str((self._call("POST", "", request).get("thread") or {}).get("id") or "")
        except HubError as exc:
            self._hub_refused(number, exc)
            return
        self.state["starts"][self.today] = self.state["starts"].get(self.today, 0) + 1
        if not thread:  # it may have started, so never retry it: hand it to the owner
            self._settle(number, {"status": "needs_owner"}, "The hub returned no thread id; the owner should check.")
            return
        note = f"Started panel `{thread}`. I'll post the verdict here when it finishes."
        self._settle(number, {"status": "started", "thread": thread}, note, STARTED)

    def _hub_refused(self, number: int, exc: HubError) -> None:
        if exc.status in RETRY_LATER or exc.status in AUTH_FAILURES:
            raise exc  # transient: retried next pass; auth: stops the pass
        detail = exc.body.get("detail") if isinstance(exc.body, dict) else exc.body
        estimate = detail.get("estimate") if isinstance(detail, dict) else None
        if isinstance(estimate, dict) and estimate.get("exceeds_threshold"):
            note = "The estimate is over the cost threshold, so this needs the owner's go-ahead. I never confirm cost."
            self._settle(number, {"status": "needs_owner"}, f"{note}\n\n{fenced(json.dumps(estimate), 'json')}")
            return
        self._reject(number, f"the hub refused the request (HTTP {exc.status})", detail)

    def _reject(self, number: int, reason: str, detail: Any = None) -> None:
        text = f"Can't start this panel: {reason}.\n\nFormat: docs/agents/panel-request-format.md in Runner_Dashboard."
        if detail is not None:
            text += "\n\n" + fenced(json.dumps(detail, default=str)[:MAX_DETAIL_CHARS], "json")
        self._settle(number, {"status": "rejected"}, text, REJECTED)

    def _settle(self, number: int, entry: dict[str, Any], body: str, label: str = NEEDS_OWNER) -> None:
        """Save the outcome before any GitHub write, so a failed write can't repeat the action."""
        if not self.dry_run:
            self.state["processed"][str(number)] = {"day": self.today, **entry}
            self._save()
        self.counts[entry["status"]] += 1
        self._write(number, body, label, close=label == DONE)

    def _write(self, number: int, body: str, label: str, close: bool) -> None:
        if self.dry_run:
            logger.info("[dry-run] #%s: would label %s%s, comment %r", number, label, " +close" * close, body[:200])
            return
        self.github.comment(self.cfg.repo, number, body)
        self.github.label(self.cfg.repo, number, label)
        if close:
            self.github.close(self.cfg.repo, number)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Barbara expert-panel relay (one pass).")
    parser.add_argument("--dry-run", action="store_true", help="Log the plan; write nothing to GitHub, hub or state.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    if not cfg.enabled and not args.dry_run:
        logger.info("panel relay is off (set BARB_PANEL_RELAY_ENABLED=1 to enable)")
        return 0
    if not cfg.allowed_authors:
        logger.warning("BARB_PANEL_RELAY_AUTHORS is empty, so every request will be skipped")
    try:
        state = load_state(cfg.state_path)
    except StateFileError as exc:
        logger.error("panel relay stopped: %s. Fix or move the file aside; nothing was started.", exc)
        return 2
    try:
        counts = PanelRelay(cfg, GhCli(), _request, state, dry_run=args.dry_run).run()
    except HubError as exc:
        logger.error("panel relay stopped: the hub refused the relay's token (HTTP %s)", exc.status)
        return 3
    logger.info("panel relay pass: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
