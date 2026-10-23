"""Operator-created draft GitHub security advisories (#2573, panel verdict q3).

Findings stay private by default. An operator may promote one finding to a
**draft** repository security advisory, and only when it is ``confirmed`` (by the
verifier or by hand) and ``high`` or ``critical``. The draft starts with a
temporary private fork, so the fix is developed out of public view
(``start_private_fork``). Nothing here runs automatically; the only caller is the
operator route ``POST /api/v1/security/findings/{id}/advisory``.

Postconditions:

* One draft per finding. A reservation row is written before the GitHub call, so
  a double click cannot create two drafts; a failed call releases it.
* A candidate patch that touches ``.github/`` or ``.oss-scanner/`` is left out of
  the advisory text (:mod:`vuln_scan.patches`), so CI and scanner configuration
  changes are always written by a person.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from staff.store import _now
from vuln_scan.patches import protected_paths

ELIGIBLE_SEVERITIES = frozenset({"critical", "high"})
Writer = Callable[[str, str, Any], Awaitable[dict[str, Any]]]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vs_advisories (
    finding_id  TEXT PRIMARY KEY,
    repo        TEXT NOT NULL,
    ghsa_id     TEXT NOT NULL,
    html_url    TEXT NOT NULL,
    actor       TEXT NOT NULL,
    created     TEXT NOT NULL
);
"""
_GHSA = re.compile(r"^GHSA(-[23456789cfghjmpqrvwx]{4}){3}$")
_URL = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/security/advisories/GHSA[-a-z0-9]+$"
)


class AdvisoryError(ValueError):
    """The finding cannot be promoted, or GitHub refused the draft."""


class AdvisoryExists(RuntimeError):
    """A draft (or a reservation for one) already exists for this finding."""


def problems(finding: Mapping[str, Any]) -> list[str]:
    """Why ``finding`` may not become an advisory; empty means eligible."""
    out = []
    if finding.get("state") != "confirmed":
        out.append("only confirmed findings can become advisories")
    if finding.get("severity") not in ELIGIBLE_SEVERITIES:
        out.append("only high or critical findings can become advisories")
    return out


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _block(title: str, text: str, lang: str = "") -> str:
    fence = _fence(text)
    return f"### {title}\n\n{fence}{lang}\n{text}\n{fence}\n"


def _inline(text: str) -> str:
    """``text`` as one inline code span that no backtick or newline can break out of."""
    flat = " ".join(str(text).split())[:300]
    longest = max((len(m) for m in re.findall(r"`+", flat)), default=0)
    tick = "`" * (longest + 1)
    return f"{tick} {flat} {tick}"


def advisory_body(finding: Mapping[str, Any], repo: str) -> dict[str, Any]:
    """The ``POST /repos/{org}/{repo}/security-advisories`` payload.

    Every model-written field is fenced (blocks) or an escaped inline span, so
    none of it can add markdown outside its own section (panel q5).
    """
    location = _inline(finding["path"]) + (
        f" ({_inline(finding['symbol'])})" if finding.get("symbol") else ""
    )
    parts = [
        "Reported by the fleet security scanner and confirmed before promotion. "
        "Model-written text below; verify before publishing.\n",
        f"**Location:** {location}\n",
        f"**Scanned commit:** {_inline(finding['scan_sha'])}\n",
        _block("Root cause", finding["root_cause"]),
    ]
    if finding.get("repro"):
        parts.append(_block("Reproduction", finding["repro"]))
    patch = finding.get("patch") or ""
    if patch and protected_paths(patch):
        parts.append(
            "### Candidate patch\n\nOmitted: it changes CI or scanner configuration "
            f"({', '.join(protected_paths(patch))}), which is always written by hand.\n"
        )
    elif patch:
        parts.append(_block("Candidate patch (unreviewed)", patch, "diff"))
    return {
        "summary": finding["title"][:1024],
        "description": "\n".join(parts),
        "severity": finding["severity"],
        "cwe_ids": [finding["cwe"]],
        "vulnerabilities": [{"package": {"ecosystem": "other", "name": repo}}],
        "start_private_fork": True,
    }


class AdvisoryStore:
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

    def get(self, finding_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vs_advisories WHERE finding_id=?", (finding_id,)
            ).fetchone()
        return dict(row) if row else None

    def reserve(self, finding_id: str, repo: str, actor: str) -> None:
        with self._lock, self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO vs_advisories VALUES (?,?,?,?,?,?)",
                    (finding_id, repo, "", "", actor, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise AdvisoryExists(finding_id) from exc

    def complete(self, finding_id: str, ghsa_id: str, html_url: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE vs_advisories SET ghsa_id=?, html_url=? WHERE finding_id=?",
                (ghsa_id, html_url, finding_id),
            )

    def release(self, finding_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "DELETE FROM vs_advisories WHERE finding_id=? AND ghsa_id=''",
                (finding_id,),
            )


async def create_draft(
    finding: Mapping[str, Any],
    *,
    org: str,
    actor: str,
    store: AdvisoryStore,
    write: Writer,
) -> dict[str, Any]:
    """Create the draft advisory for an eligible finding; one per finding."""
    reasons = problems(finding)
    if reasons:
        raise AdvisoryError("; ".join(reasons))
    repo = str(finding["repo"])
    store.reserve(str(finding["id"]), repo, actor)
    try:
        response = await write(
            f"/repos/{org}/{repo}/security-advisories",
            "POST",
            advisory_body(finding, repo),
        )
        ghsa_id = str(response.get("ghsa_id", ""))
        html_url = str(response.get("html_url", ""))
        if not _GHSA.match(ghsa_id) or not _URL.match(html_url):
            raise AdvisoryError("GitHub did not return a draft advisory")
    except BaseException:
        store.release(str(finding["id"]))
        raise
    store.complete(str(finding["id"]), ghsa_id, html_url)
    return store.get(str(finding["id"])) or {}
