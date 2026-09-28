"""Holds list — the locked-policy rules the scheduler refuses to run against (issue #1196).

A hold is a short operator sentence ("no bulk stale-queue cancel") with the
roles it applies to. The list is persisted as JSON under the dashboard config
dir and seeded, on first load, from the ``holds:`` lists in the role YAML so
the policies Barb carries in her prompt today become dashboard state.

Hold shape: ``{id, text, set_on, lifted_when, applies_to: [role | "*" |
"repo:<name>"], active, kind}``. ``matches(hold, role, repo)`` is the single
decision function the scheduler and the API share.

**Guardrail vs schedule hold (owner decision, issue #1726):** a hold seeded
from a role's ``holds:`` YAML list is a *guardrail* — it stays in the role
prompt and is shown in the UI as a standing rule, but it never blocks
scheduling. A hold created through the Holds tab, the holds API, or a
``staff.hold`` action is a *schedule* hold and does block
(``HoldsList.blocking`` only matches ``kind == "schedule"``). Holds persisted
before this distinction existed are migrated on load: one whose text
matches a hold ``seed_from_roles`` would produce today
becomes a guardrail; every other one becomes a schedule hold (the safer
default — it keeps blocking exactly as it did before this change).
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
from typing import Any, Literal

from staff.roles import RoleSpec, load_roles
from staff.store import _config_dir, _now

log = logging.getLogger("dashboard.staff.holds")

HOLDS_FILE = "staff_holds.json"
MAX_TEXT = 500
HoldKind = Literal["guardrail", "schedule"]
HOLD_KINDS: frozenset[str] = frozenset({"guardrail", "schedule"})


def holds_path() -> Path:
    return Path(os.environ.get("STAFF_HOLDS_FILE", str(_config_dir() / HOLDS_FILE))).expanduser()


def _hold_id(text: str) -> str:
    return "hold-" + hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:10]


@dataclass
class Hold:
    """One locked policy. ``applies_to`` entries: role name, ``*`` or ``repo:<name>``.

    ``kind``: ``"guardrail"`` (seeded from role YAML, never blocks scheduling)
    or ``"schedule"`` (created via the Holds tab/API/``staff.hold`` action,
    blocks scheduling). See module docstring (#1726).
    """

    id: str
    text: str
    set_on: str = field(default_factory=_now)
    lifted_when: str = ""
    applies_to: list[str] = field(default_factory=lambda: ["*"])
    active: bool = True
    kind: HoldKind = "schedule"

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_kind: HoldKind = "schedule") -> Hold:
        """Validate a JSON object into a Hold. Raises ``ValueError`` on bad input.

        ``default_kind`` is used when ``data`` carries no ``kind`` at all (a
        hold persisted before #1726, or one created by a caller that leaves
        the field unset); an explicit ``kind`` in ``data`` always wins.
        """
        text = str(data.get("text", "")).strip()
        if not text:
            raise ValueError("hold text is required")
        if len(text) > MAX_TEXT:
            raise ValueError(f"hold text longer than {MAX_TEXT} characters")
        raw_targets = data.get("applies_to") or ["*"]
        if not isinstance(raw_targets, list) or not all(isinstance(t, str) and t.strip() for t in raw_targets):
            raise ValueError("applies_to must be a list of non-empty strings")
        raw_kind = data.get("kind")
        if raw_kind is None:
            kind: HoldKind = default_kind
        else:
            kind = str(raw_kind).strip().lower()  # type: ignore[assignment]
            if kind not in HOLD_KINDS:
                raise ValueError(f"kind must be 'guardrail' or 'schedule', got {raw_kind!r}")
        hold = cls(
            id=str(data.get("id") or _hold_id(text)),
            text=text,
            set_on=str(data.get("set_on") or _now()),
            lifted_when=str(data.get("lifted_when") or ""),
            applies_to=[t.strip() for t in raw_targets],
            active=bool(data.get("active", True)),
            kind=kind,
        )
        assert hold.kind in HOLD_KINDS  # noqa: S101 — DbC boundary check (#1726)
        return hold

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
    """Build the initial list from every role's ``holds:`` list; same text merges targets.

    Every seeded hold is a guardrail (#1726): it stays in the role prompt and
    shows in the UI, but ``HoldsList.blocking`` never matches it.
    """
    by_text: dict[str, Hold] = {}
    for name in sorted(roles):
        for text in roles[name].holds:
            key = text.strip().lower()
            if not key:
                continue
            hold = by_text.get(key)
            if hold is None:
                by_text[key] = Hold(id=_hold_id(text), text=text.strip(), applies_to=[name], kind="guardrail")
            elif name not in hold.applies_to:
                hold.applies_to.append(name)
    return list(by_text.values())


def _seed_key(text: str) -> str:
    """Identity a hold uses for guardrail-migration matching: its normalised text.

    Text only, not ``applies_to``: a role that is later retired stops listing the
    hold, yet the persisted copy still names it (the live barb + orchestrator case).
    """
    return " ".join(text.split()).lower()


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
        """Read the list; seed it from the role YAML when the file does not exist yet.

        A persisted hold with no ``kind`` (written before #1726) migrates to
        ``"guardrail"`` when its text matches a hold ``seed_from_roles``
        would produce today, else to ``"schedule"`` — the
        pre-#1726 behavior, since every hold blocked scheduling back then.
        The migrated list is written back so the check runs once per file.
        """
        with self._lock:
            path = self.path
            if not path.exists():
                seeded = seed_from_roles(self._roles_loader())
                self._write(seeded)
                return seeded
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = [h for h in (raw.get("holds", []) if isinstance(raw, dict) else raw) if isinstance(h, dict)]
            except (OSError, ValueError) as exc:
                log.warning("staff: holds file %s unreadable (%s); treating as empty", path, exc)
                return []
            legacy = [h for h in items if "kind" not in h]
            seed_keys: frozenset[str] = frozenset()
            if legacy:
                try:
                    seeded = seed_from_roles(self._roles_loader())
                    seed_keys = frozenset(_seed_key(h.text) for h in seeded)
                except Exception as exc:  # noqa: BLE001 — migration lookup must never block a read
                    log.warning("staff: holds guardrail-migration lookup unavailable: %s", exc)
            holds = [
                Hold.from_dict(
                    h,
                    default_kind=("guardrail" if _seed_key(str(h.get("text", ""))) in seed_keys else "schedule"),
                )
                for h in items
            ]
            if legacy:
                self._write(holds)
            return holds

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
        """First active *schedule*-kind hold matching the role/repo, or None.

        Guardrails (seeded from role YAML) never block scheduling — owner
        decision, #1726. Use ``load()`` directly to see every hold, including
        guardrails, for display purposes.
        """
        return next((h for h in self.load() if h.kind == "schedule" and matches(h, role, repo)), None)

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
