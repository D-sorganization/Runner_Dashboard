"""Private store for security-scan findings (epic #2566, issue #2569).

Findings are model output about public repositories, so they are sensitive and
untrusted at once:

* Sensitive: they stay in this SQLite store, behind the ``security.read`` scope,
  and are never filed as public issues (panel verdict q3). An operator may promote a
  confirmed finding to a draft GitHub security advisory later (P3).
* Untrusted: :func:`validate_finding` enforces a strict schema with length caps,
  and the UI renders every free-text field as plain text.

Dedup: a finding's fingerprint is ``sha256(CWE, normalized path, symbol)``. Free-text
root cause is deliberately excluded, so rewording does not create duplicates (Argon).
A re-found finding keeps its triage state, so ``false_positive`` and
``accepted_risk`` act as persistent suppressions. A finding found again after it was
marked ``fixed`` reopens as ``new`` (a regression).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from staff.store import _now

SEVERITIES = ("critical", "high", "medium", "low", "info")
CONFIDENCES = ("high", "medium", "low")
STATES = (
    "new",
    "confirmed",
    "inconclusive",
    "false_positive",
    "accepted_risk",
    "fixed",
)
SUPPRESSED = frozenset({"false_positive", "accepted_risk"})
NOTE_REQUIRED = SUPPRESSED
CAPS = {
    "title": 200,
    "symbol": 200,
    "root_cause": 4000,
    "repro": 8000,
    "patch": 64 * 1024,
    "note": 2000,
}
_CWE = re.compile(r"^CWE-\d{1,5}$")
_PATH = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9._/@+-]{1,400}$")
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_DIGEST = re.compile(r"^(sha256:[0-9a-f]{64})?$")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vs_findings (
    id            TEXT PRIMARY KEY,
    repo          TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    title         TEXT NOT NULL,
    cwe           TEXT NOT NULL,
    severity      TEXT NOT NULL,
    confidence    TEXT NOT NULL,
    path          TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    root_cause    TEXT NOT NULL,
    repro         TEXT NOT NULL,
    patch         TEXT NOT NULL,
    state         TEXT NOT NULL,
    scan_sha      TEXT NOT NULL,
    image_digest  TEXT NOT NULL,
    scan_run_id   TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    occurrences   INTEGER NOT NULL,
    UNIQUE (repo, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_vs_findings_repo ON vs_findings(repo, state);
CREATE TABLE IF NOT EXISTS vs_triage_audit (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id  TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    actor       TEXT NOT NULL,
    note        TEXT NOT NULL,
    ts          TEXT NOT NULL
);
"""


class FindingError(ValueError):
    """A finding or triage request that violates the schema."""


@dataclass(frozen=True)
class Finding:
    title: str
    cwe: str
    severity: str
    confidence: str
    path: str
    symbol: str
    root_cause: str
    repro: str
    patch: str

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.cwe, self.path, self.symbol)


def normalize_path(path: str) -> str:
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return re.sub(r"/{2,}", "/", path)


def fingerprint(cwe: str, path: str, symbol: str) -> str:
    key = f"{cwe.strip().upper()}|{normalize_path(path).lower()}|{symbol.strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _text(raw: Mapping[str, Any], key: str, *, required: bool = True) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise FindingError(f"{key} must be a string")
    value = value.strip()
    if required and not value:
        raise FindingError(f"{key} is required")
    if len(value) > CAPS[key]:
        raise FindingError(f"{key} exceeds {CAPS[key]} characters")
    return value


def validate_finding(raw: Any) -> Finding:
    """Parse one candidate finding, raising :class:`FindingError` on any violation."""
    if not isinstance(raw, Mapping):
        raise FindingError("finding must be an object")
    cwe = str(raw.get("cwe", "")).strip().upper()
    if not _CWE.match(cwe):
        raise FindingError("cwe must look like CWE-79")
    severity = str(raw.get("severity", "")).strip().lower()
    if severity not in SEVERITIES:
        raise FindingError(f"severity must be one of {SEVERITIES}")
    confidence = str(raw.get("confidence", "")).strip().lower()
    if confidence not in CONFIDENCES:
        raise FindingError(f"confidence must be one of {CONFIDENCES}")
    path = normalize_path(str(raw.get("path", "")))
    if not _PATH.match(path):
        raise FindingError("path must be a relative repository path")
    return Finding(
        title=_text(raw, "title"),
        cwe=cwe,
        severity=severity,
        confidence=confidence,
        path=path,
        symbol=_text(raw, "symbol", required=False),
        root_cause=_text(raw, "root_cause"),
        repro=_text(raw, "repro", required=False),
        patch=_text(raw, "patch", required=False),
    )


class FindingsStore:
    """SQLite-backed findings store; one connection per call, serialized writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        repo: str,
        finding: Finding,
        *,
        scan_sha: str,
        image_digest: str = "",
        scan_run_id: str = "",
    ) -> tuple[str, bool]:
        """Insert or refresh a finding; return ``(id, created)``."""
        if not _SHA.match(scan_sha):
            raise FindingError("scan_sha must be a hex commit SHA")
        if not _DIGEST.match(image_digest):
            raise FindingError("image_digest must be empty or sha256:<64 hex>")
        now = _now()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT id, state FROM vs_findings WHERE repo=? AND fingerprint=?",
                (repo, finding.fingerprint),
            ).fetchone()
            if row is not None:
                state = "new" if row["state"] == "fixed" else row["state"]
                conn.execute(
                    "UPDATE vs_findings SET last_seen=?, occurrences=occurrences+1, "
                    "state=?, scan_sha=?, image_digest=?, scan_run_id=? WHERE id=?",
                    (now, state, scan_sha, image_digest, scan_run_id, row["id"]),
                )
                return row["id"], False
            finding_id = uuid.uuid4().hex
            fields = asdict(finding)
            conn.execute(
                "INSERT INTO vs_findings (id, repo, fingerprint, title, cwe, severity, "
                "confidence, path, symbol, root_cause, repro, patch, state, scan_sha, "
                "image_digest, scan_run_id, first_seen, last_seen, occurrences) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    finding_id,
                    repo,
                    finding.fingerprint,
                    fields["title"],
                    fields["cwe"],
                    fields["severity"],
                    fields["confidence"],
                    fields["path"],
                    fields["symbol"],
                    fields["root_cause"],
                    fields["repro"],
                    fields["patch"],
                    "new",
                    scan_sha,
                    image_digest,
                    scan_run_id,
                    now,
                    now,
                ),
            )
            return finding_id, True

    def get(self, finding_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vs_findings WHERE id=?", (finding_id,)
            ).fetchone()
            return dict(row) if row else None

    def list(
        self,
        *,
        repo: str | None = None,
        state: str | None = None,
        severity: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        for column, value in (("repo", repo), ("state", state), ("severity", severity)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = (
            "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, last_seen DESC"
        )
        sql = f"SELECT * FROM vs_findings {where} ORDER BY {order} LIMIT ?"  # noqa: S608
        with self._conn() as conn:
            rows = conn.execute(sql, (*params, max(1, min(limit, 1000)))).fetchall()
            return [dict(r) for r in rows]

    def open_counts(self) -> dict[str, dict[str, int]]:
        """Per repo, counts of open (not suppressed, not fixed) findings by severity."""
        sql = (
            "SELECT repo, severity, COUNT(*) AS n FROM vs_findings "
            "WHERE state IN ('new','confirmed','inconclusive') GROUP BY repo, severity"
        )
        counts: dict[str, dict[str, int]] = {}
        with self._conn() as conn:
            for row in conn.execute(sql):
                counts.setdefault(row["repo"], {})[row["severity"]] = row["n"]
        return counts

    def triage(
        self, finding_id: str, to_state: str, *, actor: str, note: str = ""
    ) -> dict[str, Any]:
        """Move a finding to ``to_state`` and append an audit row."""
        if to_state not in STATES:
            raise FindingError(f"state must be one of {STATES}")
        note = note.strip()
        if len(note) > CAPS["note"]:
            raise FindingError(f"note exceeds {CAPS['note']} characters")
        if to_state in NOTE_REQUIRED and not note:
            raise FindingError(f"a note is required to mark a finding {to_state}")
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT state FROM vs_findings WHERE id=?", (finding_id,)
            ).fetchone()
            if row is None:
                raise KeyError(finding_id)
            conn.execute(
                "UPDATE vs_findings SET state=? WHERE id=?", (to_state, finding_id)
            )
            conn.execute(
                "INSERT INTO vs_triage_audit "
                "(finding_id, from_state, to_state, actor, note, ts) "
                "VALUES (?,?,?,?,?,?)",
                (finding_id, row["state"], to_state, actor, note, _now()),
            )
        return self.get(finding_id) or {}

    def audit(self, finding_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT from_state, to_state, actor, note, ts FROM vs_triage_audit "
                "WHERE finding_id=? ORDER BY seq",
                (finding_id,),
            ).fetchall()
            return [dict(r) for r in rows]


_store: FindingsStore | None = None
_store_lock = threading.Lock()


def default_db_path() -> Path:
    configured = os.environ.get("SECURITY_FINDINGS_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    config_dir = os.environ.get(
        "RUNNER_DASHBOARD_CONFIG_DIR", "~/.config/runner-dashboard"
    )
    return Path(config_dir) / "security_findings.db"


def get_findings_store(path: Path | None = None) -> FindingsStore:
    """Process-wide store; the path is resolved lazily so tests can use tmp."""
    global _store  # noqa: PLW0603
    target = (path or default_db_path()).expanduser().resolve()
    with _store_lock:
        if _store is None or _store.path != target:
            _store = FindingsStore(target)
        return _store
