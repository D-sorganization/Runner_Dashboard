"""Barb Operating Manual and Playbook schema, validator, and assignment-state contract (#3486)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import yaml

SOURCE_OF_TRUTH_MODE = "repo_markdown_yaml_cache_only"
DB_IS_REBUILDABLE_CACHE = True
CLAIM_ENDPOINT = "POST /claims"
CLAIM_RELEASE_ENDPOINT = "POST /claims/release"
DEPRECATED_CLAIM_ENDPOINTS: frozenset[str] = frozenset(
    {"/api/coordination/claims", "POST /api/coordination/claims", "DELETE /api/coordination/claims"}
)
HOLDS_AUTHORITY_MODULES: tuple[str, str] = ("backend/staff/holds.py", "backend/staff/hold_actions.py")

_KEBAB_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_PARAM_TYPES: frozenset[str] = frozenset({"string", "integer", "boolean", "enum"})
_HOLD_KEYS: frozenset[str] = frozenset(
    {"hold", "holds", "hold_id", "hold_ids", "lift_hold", "override_hold", "bypass_hold", "grant_hold"}
)


class PlaybookValidationError(ValueError):
    """Raised when a manual, playbook, or state transition violates the #3486 contract."""


class PlaybookAssignmentState(StrEnum):
    """Shared assignment-state contract for playbook lifecycle (issues #3486-#3490)."""

    ASSIGNED = "ASSIGNED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    ORPHANED = "ORPHANED"


_S = PlaybookAssignmentState
TERMINAL_ASSIGNMENT_STATES: frozenset[PlaybookAssignmentState] = frozenset(
    {_S.COMPLETED, _S.FAILED, _S.TIMED_OUT, _S.ORPHANED}
)
ALLOWED_ASSIGNMENT_TRANSITIONS: dict[PlaybookAssignmentState, frozenset[PlaybookAssignmentState]] = {
    _S.ASSIGNED: frozenset({_S.ACKNOWLEDGED, _S.FAILED, _S.TIMED_OUT, _S.ORPHANED}),
    _S.ACKNOWLEDGED: frozenset({_S.RUNNING, *TERMINAL_ASSIGNMENT_STATES}),
    _S.RUNNING: TERMINAL_ASSIGNMENT_STATES,
    **dict.fromkeys(TERMINAL_ASSIGNMENT_STATES, frozenset()),
}


def validate_assignment_transition(
    current: PlaybookAssignmentState | str, target: PlaybookAssignmentState | str
) -> PlaybookAssignmentState:
    try:
        cur = current if isinstance(current, _S) else _S(str(current).strip())
        tgt = target if isinstance(target, _S) else _S(str(target).strip())
    except ValueError as exc:
        raise PlaybookValidationError(f"invalid playbook assignment state: {current!r} -> {target!r}") from exc
    if tgt not in ALLOWED_ASSIGNMENT_TRANSITIONS.get(cur, frozenset()):
        raise PlaybookValidationError(f"invalid playbook assignment state transition: {cur.value} -> {tgt.value}")
    return tgt


def compute_content_sha256(content: str | bytes | Mapping[str, Any]) -> str:
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, Mapping):
        raw = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    else:
        raise PlaybookValidationError("content must be str, bytes, or mapping")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ManualSection:
    anchor: str
    title: str
    policy_restricted: bool
    body: str
    sha256: str


@dataclass(frozen=True)
class BarbOperatingManual:
    schema_version: int
    version: str
    title: str
    claim_endpoint: str
    claim_release_endpoint: str
    holds_authority_modules: tuple[str, ...]
    sections: tuple[ManualSection, ...]
    sha256: str

    def section_by_anchor(self, anchor: str) -> ManualSection | None:
        norm = normalize_anchor(anchor)
        return next((s for s in self.sections if s.anchor == norm), None)


@dataclass(frozen=True)
class PlaybookParameter:
    name: str
    param_type: str
    required: bool = True
    default: Any = None
    description: str = ""
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaybookDefinition:
    schema_version: int
    playbook_id: str
    version: str
    title: str
    summary: str
    manual_anchors: tuple[str, ...]
    directive_refs: tuple[str, ...]
    role_refs: tuple[str, ...]
    claim_endpoint: str
    claim_release_endpoint: str
    parameters: tuple[PlaybookParameter, ...]
    steps: tuple[str, ...]
    sha256: str
    pin: str


def normalize_anchor(raw: str) -> str:
    if not isinstance(raw, str):
        raise PlaybookValidationError("anchor must be a string")
    cleaned = raw.strip().removeprefix("manual#").removeprefix("#")
    if not cleaned or not _KEBAB_ID_RE.match(cleaned):
        raise PlaybookValidationError(f"anchor must be a non-empty kebab-case identifier, got {raw!r}")
    return cleaned


def _require_str(data: Mapping[str, Any], field: str, pattern: re.Pattern[str] | None = None) -> str:
    val = data.get(field)
    if not isinstance(val, str) or not val.strip():
        raise PlaybookValidationError(f"{field} must be a non-empty string")
    cleaned = val.strip()
    if pattern is not None and not pattern.match(cleaned):
        raise PlaybookValidationError(f"{field} has invalid format: {cleaned!r}")
    return cleaned


def _parse_common_header(raw: Mapping[str, Any] | str) -> tuple[dict[str, Any], str, str, str, str]:
    if isinstance(raw, Mapping):
        data, body = dict(raw), ""
    elif isinstance(raw, str):
        stripped, body = raw.lstrip("\ufeff"), ""
        if stripped.startswith("---"):
            parts = stripped.split("---", 2)
            if len(parts) < 3:
                raise PlaybookValidationError("YAML frontmatter is not closed")
            stripped, body = parts[1], parts[2].strip()
        try:
            loaded = yaml.safe_load(stripped)
        except yaml.YAMLError as exc:
            raise PlaybookValidationError(f"invalid YAML: {exc}") from exc
        if not isinstance(loaded, dict):
            raise PlaybookValidationError("parsed YAML must be a mapping")
        data = loaded
    else:
        raise PlaybookValidationError("input must be a mapping or YAML/markdown string")

    for key in data:
        low = str(key).lower()
        if str(key) in _HOLD_KEYS or low.startswith("hold_") or low.endswith("_hold"):
            raise PlaybookValidationError(f"hold mutation field {key!r} is forbidden in playbook/manual schemas")
    if data.get("schema_version") != 1:
        raise PlaybookValidationError("schema_version must be 1")
    version = _require_str(data, "version", _SEMVER_RE)
    c_ep = str(data.get("claim_endpoint") or CLAIM_ENDPOINT).strip()
    r_ep = str(data.get("claim_release_endpoint") or CLAIM_RELEASE_ENDPOINT).strip()
    if c_ep != CLAIM_ENDPOINT or r_ep != CLAIM_RELEASE_ENDPOINT:
        raise PlaybookValidationError(f"claim endpoints must be {CLAIM_ENDPOINT!r} and {CLAIM_RELEASE_ENDPOINT!r}")
    return data, body, version, c_ep, r_ep


def parse_operating_manual(raw: Mapping[str, Any] | str) -> BarbOperatingManual:
    data, _, version, claim_ep, release_ep = _parse_common_header(raw)
    title = _require_str(data, "title")
    holds_mods = data.get("holds_authority_modules", list(HOLDS_AUTHORITY_MODULES))
    if not isinstance(holds_mods, (list, tuple)) or tuple(holds_mods) != HOLDS_AUTHORITY_MODULES:
        raise PlaybookValidationError(f"holds_authority_modules must match {HOLDS_AUTHORITY_MODULES!r}")
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise PlaybookValidationError("sections must be a non-empty list")
    sections, seen = [], set()
    for idx, item in enumerate(raw_sections):
        if not isinstance(item, Mapping):
            raise PlaybookValidationError(f"sections[{idx}] must be a mapping")
        anchor, body = normalize_anchor(_require_str(item, "anchor")), _require_str(item, "body")
        if anchor in seen:
            raise PlaybookValidationError(f"duplicate manual section anchor: {anchor!r}")
        seen.add(anchor)
        sec_title, restricted = _require_str(item, "title"), bool(item.get("policy_restricted", True))
        sections.append(ManualSection(anchor, sec_title, restricted, body, compute_content_sha256(body)))
    digest = compute_content_sha256({"version": version, "title": title, "sections": [s.sha256 for s in sections]})
    mods = HOLDS_AUTHORITY_MODULES
    return BarbOperatingManual(1, version, title, claim_ep, release_ep, mods, tuple(sections), digest)


def parse_playbook(raw: Mapping[str, Any] | str) -> PlaybookDefinition:
    data, body_text, version, claim_ep, release_ep = _parse_common_header(raw)
    pb_id = _require_str(data, "playbook_id", _KEBAB_ID_RE)
    title, summary = _require_str(data, "title"), _require_str(data, "summary")
    for field in ("manual_anchors", "directive_refs", "role_refs"):
        if not isinstance(data.get(field), list) or not data.get(field):
            raise PlaybookValidationError(f"{field} must be a non-empty list")
    anchors = tuple(normalize_anchor(a) for a in data["manual_anchors"])
    d_refs = tuple(_require_str({"v": x}, "v", _KEBAB_ID_RE) for x in data["directive_refs"])
    r_refs = tuple(_require_str({"v": x}, "v", _KEBAB_ID_RE) for x in data["role_refs"])

    raw_params = data.get("parameters") or []
    if not isinstance(raw_params, list):
        raise PlaybookValidationError("parameters must be a list")
    params, seen = [], set()
    for idx, item in enumerate(raw_params):
        if not isinstance(item, Mapping):
            raise PlaybookValidationError(f"parameters[{idx}] must be a mapping")
        name, ptype = _require_str(item, "name", _PARAM_NAME_RE), _require_str(item, "param_type")
        if name in seen or ptype not in _PARAM_TYPES:
            raise PlaybookValidationError(f"invalid parameter {name!r} or param_type {ptype!r}")
        seen.add(name)
        choices_raw = item.get("choices") or []
        if not isinstance(choices_raw, list) or not all(isinstance(c, str) and c.strip() for c in choices_raw):
            raise PlaybookValidationError(f"parameters[{idx}].choices must be a list of strings")
        choices = tuple(c.strip() for c in choices_raw)
        if ptype == "enum" and not choices:
            raise PlaybookValidationError(f"parameters[{idx}] of type 'enum' requires choices")
        req, desc = bool(item.get("required", True)), str(item.get("description") or "").strip()
        params.append(PlaybookParameter(name, ptype, req, item.get("default"), desc, choices))

    raw_steps = data.get("steps")
    if raw_steps is None and body_text:
        steps = tuple(line.strip() for line in body_text.splitlines() if line.strip())
    elif isinstance(raw_steps, list) and raw_steps and all(isinstance(s, str) and s.strip() for s in raw_steps):
        steps = tuple(s.strip() for s in raw_steps)
    else:
        raise PlaybookValidationError("steps must be a non-empty list of strings")
    digest = compute_content_sha256({"id": pb_id, "v": version, "anchors": list(anchors), "steps": list(steps)})
    pin = f"{pb_id}@{version}+{digest[:12]}"
    return PlaybookDefinition(
        1,
        pb_id,
        version,
        title,
        summary,
        anchors,
        d_refs,
        r_refs,
        claim_ep,
        release_ep,
        tuple(params),
        steps,
        digest,
        pin,
    )
