"""Parse and check a panel request filed as a barbara issue (#3711). Pure: no network, no model.

An allowlisted author (the owner, a Claude or Codex fleet bot) opens an issue in the private
barbara repo, labelled ``panel-request``, whose body holds one fenced block::

    ```json panel-request
    {"topic": "...", "preset": "engineering-review", "rounds": 2}
    ```

The block is data. Only the keys in ``ALLOWED_KEYS`` pass, exactly one of ``preset`` and ``experts``
is required, and nothing here can set the hub's cost confirmation or moderator. The hub's strict
``PanelCreateRequest`` model still validates every field when the relay posts the body.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

FENCE_RE = re.compile(r"^```json[ \t]+panel-request[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", re.DOTALL | re.MULTILINE)
ALLOWED_KEYS = frozenset({"topic", "preset", "experts", "rounds", "mode", "title", "repos", "refs", "quorum"})
EXPERT_KEYS = ("name", "perspective", "provider", "model")
MAX_BODY_CHARS = 20_000
PRESET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Who may file a request. Owner, plus the fleet App bots that Claude and Codex agents write as
# (Repository_Management shared_scripts/agent_identity.py; codex-scheduled-tasks since 2026-08-20).
DEFAULT_AUTHORS = (
    "dieterolson",
    "d-sorgclaudeagent[bot]",
    "d-sorgcodexagent[bot]",
    "codex-scheduled-tasks[bot]",
)


class RequestRejected(ValueError):
    """The request can't be started; the message is safe to post back on the issue."""


@dataclass(frozen=True)
class PanelRequest:
    """A checked request: the hub body minus experts, plus either a preset id or explicit experts."""

    fields: dict[str, Any]
    preset: str | None
    experts: list[dict[str, Any]] | None


def parse_authors(raw: str | None) -> frozenset[str]:
    """Comma-separated logins from the environment, or the defaults; compared case-insensitively."""
    names = [n.strip() for n in (raw or "").split(",") if n.strip()] or list(DEFAULT_AUTHORS)
    return frozenset(n.lower() for n in names)


def extract_block(body: str | None) -> dict[str, Any]:
    """The single fenced ``json panel-request`` object in ``body``."""
    text = body or ""
    if len(text) > MAX_BODY_CHARS:
        raise RequestRejected(f"issue body is over {MAX_BODY_CHARS} characters")
    blocks = FENCE_RE.findall(text)
    if len(blocks) != 1:
        raise RequestRejected(f"expected exactly one ```json panel-request block, found {len(blocks)}")
    try:
        data = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise RequestRejected(f"panel-request block is not valid JSON ({exc.msg} at line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise RequestRejected("panel-request block must be a JSON object")
    return data


def check_request(data: dict[str, Any]) -> PanelRequest:
    """Keep only allowed keys; require a topic and exactly one of ``preset`` / ``experts``."""
    extra = sorted(set(data) - ALLOWED_KEYS)
    if extra:
        raise RequestRejected(f"keys not allowed: {', '.join(extra)} (allowed: {', '.join(sorted(ALLOWED_KEYS))})")
    topic = data.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise RequestRejected("topic is required")
    has_preset, has_experts = "preset" in data, "experts" in data
    if has_preset == has_experts:
        raise RequestRejected("give exactly one of preset or experts")
    preset = data.get("preset")
    if has_preset and (not isinstance(preset, str) or not PRESET_RE.fullmatch(preset)):
        raise RequestRejected("preset must be a preset id such as engineering-review")
    experts = _check_experts(data["experts"]) if has_experts else None
    fields = {k: v for k, v in data.items() if k not in ("preset", "experts")}
    return PanelRequest(fields=fields, preset=preset if has_preset else None, experts=experts)


def _check_experts(experts: Any) -> list[dict[str, Any]]:
    if not isinstance(experts, list) or not all(isinstance(e, dict) for e in experts):
        raise RequestRejected("experts must be a list of objects")
    for seat in experts:
        unknown = sorted(set(seat) - set(EXPERT_KEYS))
        if unknown:
            raise RequestRejected(f"expert keys not allowed: {', '.join(unknown)}")
    return [dict(e) for e in experts]


def resolve_experts(request: PanelRequest, presets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Explicit experts, or the named preset's seats; refuse a preset with a seat the hub has switched off."""
    if request.experts is not None:
        return request.experts
    match = next((p for p in presets if p.get("id") == request.preset), None)
    if match is None:
        known = ", ".join(sorted(str(p.get("id")) for p in presets))
        raise RequestRejected(f"unknown preset {request.preset!r} (known: {known})")
    seats = match.get("experts") or []
    off = [str(s.get("name")) for s in seats if s.get("available") is False]
    if off:
        raise RequestRejected(f"preset {request.preset!r} has seats whose provider is off: {', '.join(off)}")
    return [{k: s[k] for k in EXPERT_KEYS if s.get(k) is not None} for s in seats]


def hub_body(request: PanelRequest, experts: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``POST /api/v1/staff/panels`` body. Never carries ``confirm_cost``."""
    body = {**request.fields, "experts": experts}
    assert "confirm_cost" not in body  # ALLOWED_KEYS excludes it; keep the invariant explicit
    return body


def fenced(text: str, info: str = "text") -> str:
    """Wrap untrusted text in a code fence longer than any backtick run inside it (no mentions, no links)."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text}\n{fence}"
