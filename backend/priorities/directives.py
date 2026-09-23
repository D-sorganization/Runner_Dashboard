"""Operator directives — short focus statements agents read with their priorities (issue #1227).

A directive is policy, not a message: "finish the coordination API before new
Staff Hub roles", optionally scoped to one repository, with a priority
(1 = highest … 5) and an optional expiry. The list is dashboard state stored as
JSON under the dashboard config dir, exactly like ``staff.holds``
(``STAFF_DIRECTIVES_FILE`` overrides the path). Expired directives stay on
disk until the next PUT but are filtered from every read.

Directive text is pasted into every staff prompt (``staff.focus``), so it is one
line: CR/LF is rejected. Writers pass ``expected_version`` (``version()`` of the
list they edited) so two operators cannot silently overwrite each other (#1243).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.store import _config_dir, _now

log = logging.getLogger("dashboard.priorities.directives")

DIRECTIVES_FILE = "staff_directives.json"
MAX_TEXT = 500
MAX_SET_BY = 120
MIN_PRIORITY, MAX_PRIORITY, DEFAULT_PRIORITY = 1, 5, 3
ALL_REPOS = "*"
_BARE_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def directives_path() -> Path:
    return Path(os.environ.get("STAFF_DIRECTIVES_FILE", str(_config_dir() / DIRECTIVES_FILE))).expanduser()


def _directive_id(text: str, repo: str) -> str:
    key = f"{text.strip().lower()}|{repo}"
    return "dir-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_expiry(value: Any) -> str:
    """``""`` for no expiry, else the instant normalised to UTC ``...Z``. Naive times are UTC."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"expires must be an ISO-8601 timestamp, got {text!r}") from exc
    return _iso_z(parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))


def _parse_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("directive text is required")
    if len(text) > MAX_TEXT:
        raise ValueError(f"directive text longer than {MAX_TEXT} characters")
    if "\r" in text or "\n" in text:
        raise ValueError("directive text must be a single line (no CR/LF); put checklists in the issue")
    return text


def _parse_repo(value: Any) -> str:
    repo = str(value or ALL_REPOS).strip()
    if repo != ALL_REPOS and (not _BARE_REPO.match(repo) or repo in (".", "..")):
        raise ValueError("repo must be '*' or a bare repository name (no owner, no path)")
    return repo


def _parse_priority(value: Any) -> int:
    if value is None:
        return DEFAULT_PRIORITY
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("priority must be an integer")
    if not MIN_PRIORITY <= value <= MAX_PRIORITY:
        raise ValueError(f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}")
    return value


@dataclass
class Directive:
    """One operator directive. ``repo`` is ``*`` for fleet-wide; ``expires`` ``""`` means never."""

    id: str
    text: str
    set_by: str
    repo: str = ALL_REPOS
    priority: int = DEFAULT_PRIORITY
    expires: str = ""
    set_on: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Directive:
        """Validate a JSON object into a Directive. Raises ``ValueError`` on bad input."""
        text = _parse_text(data.get("text"))
        set_by = str(data.get("set_by") or "").strip()
        if not set_by or len(set_by) > MAX_SET_BY:
            raise ValueError(f"set_by is required (at most {MAX_SET_BY} characters)")
        repo = _parse_repo(data.get("repo"))
        return cls(
            id=str(data.get("id") or _directive_id(text, repo)),
            text=text,
            set_by=set_by,
            repo=repo,
            priority=_parse_priority(data.get("priority")),
            expires=_parse_expiry(data.get("expires")),
            set_on=str(data.get("set_on") or _now()),
        )

    def is_active(self, now: datetime) -> bool:
        """True until ``expires`` (exclusive); directives without expiry never lapse."""
        if not self.expires:
            return True
        return datetime.fromisoformat(self.expires.replace("Z", "+00:00")) > now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VersionConflictError(Exception):
    """The stored list changed since the caller read ``version``; nothing was written."""


def _version_of(directives: list[Directive]) -> str:
    canonical = json.dumps([d.to_dict() for d in directives], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class DirectivesList:
    """JSON-file-backed directive list with process-wide locking (mirrors ``staff.holds.HoldsList``)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path or directives_path()

    def load(self) -> list[Directive]:
        """Every valid stored directive, expired included.

        A missing or unparseable file reads as empty; one invalid entry is skipped (and logged),
        never the whole list, so the next save cannot wipe the valid ones.
        """
        with self._lock:
            path = self.path
            if not path.exists():
                return []
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("priorities: directives file %s unreadable (%s); treating as empty", path, exc)
                return []
            items = raw.get("directives", []) if isinstance(raw, dict) else raw
            return [d for i, item in enumerate(items if isinstance(items, list) else []) if (d := _valid(item, i))]

    def version(self) -> str:
        """Opaque fingerprint of the stored list; changes on every effective write."""
        with self._lock:
            return _version_of(self.load())

    def active(self, now: datetime | None = None) -> list[Directive]:
        """Unexpired directives, highest priority (lowest number) first, then oldest first."""
        moment = now or datetime.now(UTC)
        return sorted((d for d in self.load() if d.is_active(moment)), key=lambda d: (d.priority, d.set_on))

    def replace(
        self,
        items: list[dict[str, Any]],
        *,
        author: str | None = None,
        expected_version: str | None = None,
    ) -> list[Directive]:
        """Replace the whole list (PUT semantics).

        ``author`` (the authenticated caller) overrides any ``set_by`` in ``items``, except that a
        directive already stored unchanged keeps its stored author. Raises ``ValueError`` on bad
        input and ``VersionConflictError`` when ``expected_version`` is stale; both before writing.
        """
        with self._lock:
            stored = self.load()
            if expected_version is not None and expected_version != _version_of(stored):
                raise VersionConflictError("directives changed since you loaded them; reload and re-apply")
            directives = [Directive.from_dict(_attributed(d, author, stored)) for d in items]
            seen: set[str] = set()
            for directive in directives:
                if directive.id in seen:
                    raise ValueError(f"duplicate directive id {directive.id}")
                seen.add(directive.id)
            self._write(directives)
        return directives

    def _write(self, directives: list[Directive]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"directives": [d.to_dict() for d in directives], "updated_at": _now()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def _valid(item: Any, index: int) -> Directive | None:
    try:
        if not isinstance(item, dict):
            raise ValueError("not an object")
        return Directive.from_dict(item)
    except ValueError as exc:
        log.warning("priorities: skipping invalid directive #%d (%s)", index, exc)
        return None


def _attributed(item: dict[str, Any], author: str | None, stored: list[Directive]) -> dict[str, Any]:
    """``item`` with ``set_by`` taken from the server: the stored author when unchanged, else ``author``."""
    if author is None:
        return item
    text = str(item.get("text") or "").strip()
    repo = str(item.get("repo") or ALL_REPOS).strip()
    same = next((d for d in stored if d.text == text and d.repo == repo and d.id == item.get("id", d.id)), None)
    return {**item, "set_by": same.set_by if same else author}


_DIRECTIVES = DirectivesList()


def get_directives() -> DirectivesList:
    """The process-wide list; its path is re-resolved from the environment on every access."""
    return _DIRECTIVES
