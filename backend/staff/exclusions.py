"""Dispatch exclusions - durable repo/issue lanes the dashboard refuses to dispatch onto.

An exclusion marks a repo (or one issue in it, optionally narrowed by a path
glob) as owned by a peer session or lane. Dispatch of a staff run whose target
matches an active, unexpired exclusion is refused with 409, and the scheduler
skips the slot with the reason logged. This is the dashboard-side replacement
for the orchestrator's ``DO_NOT_DISPATCH.txt`` file.

Entry shape: ``{id, repo, issue (int | null = whole repo), path_glob (optional),
owner, reason, created_by, created_at, expires_at (optional)}``.

Matching (``matches``): repo compares by name, case-insensitively, ignoring any
``org/`` prefix. ``issue`` null matches every issue. A ``path_glob`` only matches
when the dispatch declares paths (backtick paths in the prompt) and one of them
fnmatches; a dispatch with no declared paths is not blocked by a glob-narrowed
entry (it cannot be shown to touch the lane).

Persistence mirrors ``staff.holds``: one JSON file under the dashboard config dir
(``STAFF_EXCLUSIONS_FILE`` overrides), atomic replace, process-wide RLock.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.store import _config_dir, _now

log = logging.getLogger("dashboard.staff.exclusions")

EXCLUSIONS_FILE = "staff_exclusions.json"
MAX_TEXT = 500
MAX_EXCLUSIONS = 500


def exclusions_path() -> Path:
    return Path(os.environ.get("STAFF_EXCLUSIONS_FILE", str(_config_dir() / EXCLUSIONS_FILE))).expanduser()


def normalize_repo(repo: str) -> str:
    """Comparison key for a repo: lower-cased name without any ``org/`` prefix."""
    return repo.strip().rsplit("/", 1)[-1].lower()


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _exclusion_id(repo: str, issue: int | None, path_glob: str) -> str:
    key = f"{normalize_repo(repo)}#{issue if issue is not None else '*'}:{path_glob}"
    return "excl-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


@dataclass
class Exclusion:
    """One dispatch exclusion. See the module docstring for field semantics."""

    id: str
    repo: str
    owner: str
    reason: str
    issue: int | None = None
    path_glob: str = ""
    created_by: str = ""
    created_at: str = ""
    expires_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], created_by: str = "") -> Exclusion:
        """Validate a JSON object into an Exclusion. Raises ``ValueError`` on bad input."""
        repo = str(data.get("repo") or "").strip()
        if not repo:
            raise ValueError("repo is required")
        owner = str(data.get("owner") or "").strip()
        if not owner:
            raise ValueError("owner is required")
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise ValueError("reason is required")
        if len(owner) > MAX_TEXT or len(reason) > MAX_TEXT:
            raise ValueError(f"owner/reason longer than {MAX_TEXT} characters")
        raw_issue = data.get("issue")
        issue: int | None = None
        if raw_issue is not None:
            if isinstance(raw_issue, bool) or not isinstance(raw_issue, int) or raw_issue < 1:
                raise ValueError("issue must be a positive integer or null")
            issue = raw_issue
        path_glob = str(data.get("path_glob") or "").strip()
        expires_raw = data.get("expires_at")
        expires_at: str | None = None
        if expires_raw:
            parsed = _parse_ts(str(expires_raw))
            if parsed is None:
                raise ValueError("expires_at must be an ISO-8601 timestamp")
            expires_at = str(expires_raw).strip()
        return cls(
            id=str(data.get("id") or _exclusion_id(repo, issue, path_glob)),
            repo=repo,
            owner=owner,
            reason=reason,
            issue=issue,
            path_glob=path_glob,
            created_by=str(data.get("created_by") or created_by),
            created_at=str(data.get("created_at") or _now()),
            expires_at=expires_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_active(self, now: datetime | None = None) -> bool:
        """False once ``expires_at`` has passed (an unparseable expiry never blocks)."""
        if not self.expires_at:
            return True
        expiry = _parse_ts(self.expires_at)
        if expiry is None:
            return False
        return (now or datetime.now(UTC)) < expiry


def matches(
    entry: Exclusion,
    repo: str,
    issue: int | None = None,
    paths: Iterable[str] = (),
    now: datetime | None = None,
) -> bool:
    """True when an active, unexpired exclusion covers this dispatch target."""
    if not repo or not entry.is_active(now):
        return False
    if normalize_repo(entry.repo) != normalize_repo(repo):
        return False
    if entry.issue is not None and entry.issue != issue:
        return False
    if entry.path_glob:
        return any(fnmatch.fnmatch(p.lstrip("/"), entry.path_glob.lstrip("/")) for p in paths)
    return True


class ExclusionsList:
    """JSON-file-backed exclusions list with process-wide locking."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path or exclusions_path()

    def load(self) -> list[Exclusion]:
        with self._lock:
            path = self.path
            if not path.exists():
                return []
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = [
                    e for e in (raw.get("exclusions", []) if isinstance(raw, dict) else raw) if isinstance(e, dict)
                ]
                return [Exclusion.from_dict(e) for e in items]
            except (OSError, ValueError) as exc:
                log.warning("staff: exclusions file %s unreadable (%s); treating as empty", path, exc)
                return []

    def active(self, now: datetime | None = None) -> list[Exclusion]:
        return [e for e in self.load() if e.is_active(now)]

    def add(self, data: dict[str, Any], created_by: str = "") -> Exclusion:
        """Create (or replace the same repo/issue/glob entry). Raises ``ValueError`` on bad input."""
        entry = Exclusion.from_dict(
            {k: v for k, v in data.items() if k not in ("created_by", "created_at")}, created_by
        )
        with self._lock:
            items = [e for e in self.load() if e.id != entry.id]
            if len(items) >= MAX_EXCLUSIONS:
                raise ValueError(f"more than {MAX_EXCLUSIONS} exclusions")
            items.append(entry)
            self._write(items)
        return entry

    def remove(self, exclusion_id: str) -> bool:
        with self._lock:
            items = self.load()
            kept = [e for e in items if e.id != exclusion_id]
            if len(kept) == len(items):
                return False
            self._write(kept)
            return True

    def blocking(
        self,
        repo: str,
        issue: int | None = None,
        paths: Iterable[str] = (),
        now: datetime | None = None,
    ) -> Exclusion | None:
        """First active exclusion covering (repo, issue, paths), or None."""
        paths = list(paths)
        return next((e for e in self.load() if matches(e, repo, issue, paths, now)), None)

    def _write(self, items: list[Exclusion]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"exclusions": [e.to_dict() for e in items], "updated_at": _now()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def refusal_detail(entry: Exclusion) -> dict[str, Any]:
    """The 409 body naming who owns the lane, why, and until when."""
    target = entry.repo + (f"#{entry.issue}" if entry.issue is not None else "")
    return {
        "code": "dispatch_excluded",
        "exclusion_id": entry.id,
        "repo": entry.repo,
        "issue": entry.issue,
        "owner": entry.owner,
        "reason": entry.reason,
        "expires_at": entry.expires_at,
        "message": f"{target} is excluded from dispatch (owner: {entry.owner}): {entry.reason}",
    }


def active_exclusions() -> list[dict[str, Any]]:
    """Active exclusions as plain dicts, for read-only views such as ``GET /api/staff/summary``."""
    return [e.to_dict() for e in ExclusionsList().active()]
