"""Holds list — the locked-policy rules the scheduler refuses to run against (issue #1196).

A hold is a short operator sentence ("no bulk stale-queue cancel") with the
roles it applies to. The list is persisted as JSON under the dashboard config
dir and seeded, on first load, from the ``holds:`` lists in the role YAML so
the policies Barb carries in her prompt today become dashboard state.

Hold shape: ``{id, text, set_on, lifted_when, applies_to: [role | "*" |
"repo:<name>"], active}``. ``matches(hold, role, repo)`` is the single
decision function the scheduler and the API share.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from staff.roles import RoleSpec, load_roles
from staff.store import _config_dir, _now

log = logging.getLogger("dashboard.staff.holds")

HOLDS_FILE = "staff_holds.json"
MAX_TEXT = 500


def holds_path() -> Path:
    return Path(os.environ.get("STAFF_HOLDS_FILE", str(_config_dir() / HOLDS_FILE))).expanduser()


def _hold_id(text: str) -> str:
    return "hold-" + hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:10]


@dataclass
class Hold:
    """One locked policy. ``applies_to`` entries: role name, ``*`` or ``repo:<name>``."""

    id: str
    text: str
    set_on: str = field(default_factory=_now)
    lifted_when: str = ""
    applies_to: list[str] = field(default_factory=lambda: ["*"])
    active: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hold:
        """Validate a JSON object into a Hold. Raises ``ValueError`` on bad input."""
        text = str(data.get("text", "")).strip()
        if not text:
            raise ValueError("hold text is required")
        if len(text) > MAX_TEXT:
            raise ValueError(f"hold text longer than {MAX_TEXT} characters")
        raw_targets = data.get("applies_to") or ["*"]
        if not isinstance(raw_targets, list) or not all(isinstance(t, str) and t.strip() for t in raw_targets):
            raise ValueError("applies_to must be a list of non-empty strings")
        return cls(
            id=str(data.get("id") or _hold_id(text)),
            text=text,
            set_on=str(data.get("set_on") or _now()),
            lifted_when=str(data.get("lifted_when") or ""),
            applies_to=[t.strip() for t in raw_targets],
            active=bool(data.get("active", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def matches(hold: Hold, role: str, repo: str = "") -> bool:
    """True when an *active* hold applies to ``role`` (or the repo it targets)."""
    if not hold.active:
        return False
    for target in hold.applies_to:
        if target == "*" or target == role:
            return True
        if repo and target.startswith("repo:") and target[5:] == repo:
            return True
    return False


def seed_from_roles(roles: dict[str, RoleSpec]) -> list[Hold]:
    """Build the initial list from every role's ``holds:`` list; same text merges targets."""
    by_text: dict[str, Hold] = {}
    for name in sorted(roles):
        for text in roles[name].holds:
            key = text.strip().lower()
            if not key:
                continue
            hold = by_text.get(key)
            if hold is None:
                by_text[key] = Hold(id=_hold_id(text), text=text.strip(), applies_to=[name])
            elif name not in hold.applies_to:
                hold.applies_to.append(name)
    return list(by_text.values())


class HoldsList:
    """JSON-file-backed holds list with process-wide locking."""

    def __init__(self, path: Path | None = None, roles_loader: Callable[[], dict[str, RoleSpec]] = load_roles) -> None:
        self._path = path
        self._roles_loader = roles_loader
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path or holds_path()

    def load(self) -> list[Hold]:
        """Read the list; seed it from the role YAML when the file does not exist yet."""
        with self._lock:
            path = self.path
            if not path.exists():
                seeded = seed_from_roles(self._roles_loader())
                self._write(seeded)
                return seeded
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = raw.get("holds", []) if isinstance(raw, dict) else raw
                return [Hold.from_dict(h) for h in items if isinstance(h, dict)]
            except (OSError, ValueError) as exc:
                log.warning("staff: holds file %s unreadable (%s); treating as empty", path, exc)
                return []

    def replace(self, items: list[dict[str, Any]]) -> list[Hold]:
        """Replace the whole list (PUT semantics). Raises ``ValueError`` on bad input."""
        holds = [Hold.from_dict(h) for h in items]
        seen: set[str] = set()
        for hold in holds:
            if hold.id in seen:
                raise ValueError(f"duplicate hold id {hold.id}")
            seen.add(hold.id)
        with self._lock:
            self._write(holds)
        return holds

    def blocking(self, role: str, repo: str = "") -> Hold | None:
        """First active hold matching the role/repo, or None."""
        return next((h for h in self.load() if matches(h, role, repo)), None)

    def _write(self, holds: list[Hold]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"holds": [h.to_dict() for h in holds], "updated_at": _now()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def active_holds() -> list[dict[str, Any]]:
    """Active holds as plain dicts, for read-only views such as ``GET /api/staff/summary``."""
    return [h.to_dict() for h in HoldsList().load() if h.active]
