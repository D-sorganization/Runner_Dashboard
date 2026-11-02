"""Barbara expert-panel relay (#3711 slice 2). Off by default."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PANEL_BLOCK = re.compile(r"```panel-request\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_TRUTHY = frozenset({"1", "true", "yes", "on"})
PANELS = "/api/v1/staff/panels"


@dataclass(frozen=True)
class RelayConfig:
    enabled: bool
    hub_url: str
    token: str
    allowed_authors: frozenset[str]
    daily_cap: int
    state_path: Path


def load_config(env: dict[str, str] | None = None) -> RelayConfig:
    raw = os.environ if env is None else env
    authors = frozenset(a.strip().lower() for a in (raw.get("BARB_PANEL_RELAY_AUTHORS") or "").split(",") if a.strip())
    return RelayConfig(
        enabled=(raw.get("BARB_PANEL_RELAY_ENABLED") or "").strip().lower() in _TRUTHY,
        hub_url=(raw.get("FLEET_API_URL") or "http://127.0.0.1:8321").rstrip("/"),
        token=(raw.get("FLEET_API_TOKEN") or "").strip(),
        allowed_authors=authors,
        daily_cap=max(0, int(raw.get("BARB_PANEL_RELAY_DAILY_CAP") or "3")),
        state_path=Path(raw.get("BARB_PANEL_RELAY_STATE") or Path.home() / ".config/runner-dashboard/barb-panel-relay.json"),
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


def _load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"processed": {}, "count": 0}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _request(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    data = None if body is None else json.dumps(body).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
        raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else {}


def process_issue(
    issue: dict[str, Any],
    *,
    cfg: RelayConfig,
    state: dict[str, Any],
    hub: Any | None = None,
) -> dict[str, Any]:
    number = issue.get("number")
    author = ((issue.get("user") or {}).get("login") or "").lower()
    if cfg.allowed_authors and author not in cfg.allowed_authors:
        return {"issue": number, "status": "skipped", "reason": "author_not_allowed"}
    if str(number) in (state.get("processed") or {}):
        return {"issue": number, "status": "deduped"}
    if cfg.daily_cap and int(state.get("count") or 0) >= cfg.daily_cap:
        return {"issue": number, "status": "skipped", "reason": "daily_cap"}
    if not cfg.token:
        return {"issue": number, "status": "error", "reason": "missing_token"}
    request = parse_panel_request(issue.get("body") or "")
    call = hub or _request
    started = call("POST", f"{cfg.hub_url}{PANELS}", cfg.token, request)
    thread = (started.get("thread") or {}).get("id") or ""
    if not thread:
        return {"issue": number, "status": "error", "reason": "no_thread_id"}
    try:
        digest = call("GET", f"{cfg.hub_url}{PANELS}/{thread}/digest", cfg.token, None)
    except urllib.error.HTTPError:
        digest = {"thread": thread, "status": "pending"}
    state.setdefault("processed", {})[str(number)] = {"thread": thread}
    state["count"] = int(state.get("count") or 0) + 1
    return {"issue": number, "status": "started", "thread": thread, "digest": digest}


def run_once(cfg: RelayConfig, issues: list[dict[str, Any]], hub: Any | None = None) -> list[dict[str, Any]]:
    if not cfg.enabled:
        return [{"status": "disabled"}]
    state = _load_state(cfg.state_path)
    results = [process_issue(issue, cfg=cfg, state=state, hub=hub) for issue in issues]
    _save_state(cfg.state_path, state)
    return results
