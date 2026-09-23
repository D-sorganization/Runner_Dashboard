"""File readers for the RM checkout: board-meeting folders and portfolios (issue #1227).

Every function takes the RM root explicitly (the service resolves it via
``staff.workspace.rm_root``) so these stay pure path logic and are testable
against a temp tree. RM code is never imported; only its files are read.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from priorities.consensus import parse_consensus
from priorities.models import Consensus, Portfolio

log = logging.getLogger("dashboard.priorities")

MEETINGS_DIR = Path("docs") / "board-meetings"
MANIFEST_PATH = Path("config") / "fleet_manifest.yaml"
MEETING_FILES = ("packet.md", "consensus.md", "instructions.md", "pathway-log-entry.md")
MAX_RAW_CHARS = 200_000
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_DATE = re.compile(DATE_PATTERN)


class SourceUnavailable(Exception):
    """An RM file the caller needs is missing or unreadable; the message is the API ``reason``."""


def valid_meeting_date(value: str) -> bool:
    """True for a real calendar date in ``YYYY-MM-DD`` form."""
    if not _DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text if len(text) <= MAX_RAW_CHARS else text[:MAX_RAW_CHARS] + "\n[truncated]"


def list_meetings(rm_root: Path) -> list[dict[str, Any]]:
    """Dated meeting folders, newest first: ``[{date, files, has_consensus}]``."""
    base = rm_root / MEETINGS_DIR
    if not base.is_dir():
        return []
    meetings = []
    for folder in base.iterdir():
        if not folder.is_dir() or not valid_meeting_date(folder.name):
            continue
        files = [name for name in MEETING_FILES if (folder / name).is_file()]
        meetings.append({"date": folder.name, "files": files, "has_consensus": "consensus.md" in files})
    return sorted(meetings, key=lambda m: m["date"], reverse=True)


def latest_consensus(rm_root: Path) -> tuple[str, Consensus] | None:
    """The newest meeting that has a readable ``consensus.md``, parsed; None when there is none."""
    for meeting in list_meetings(rm_root):
        if not meeting["has_consensus"]:
            continue
        text = _read(rm_root / MEETINGS_DIR / meeting["date"] / "consensus.md")
        if text is not None:
            return meeting["date"], parse_consensus(text)
    return None


def read_meeting(rm_root: Path, meeting_date: str) -> dict[str, Any] | None:
    """One meeting: parsed consensus plus raw markdown of every file; None when the folder is absent.

    Precondition: ``meeting_date`` is a valid ``YYYY-MM-DD`` date (ValueError otherwise), which also
    rules out path traversal.
    """
    if not valid_meeting_date(meeting_date):
        raise ValueError(f"meeting date must be YYYY-MM-DD, got {meeting_date!r}")
    folder = rm_root / MEETINGS_DIR / meeting_date
    if not folder.is_dir():
        return None
    raw = {name: _read(folder / name) for name in MEETING_FILES}
    consensus_text = raw["consensus.md"]
    return {
        "date": meeting_date,
        "files": [name for name, text in raw.items() if text is not None],
        "consensus": parse_consensus(consensus_text).model_dump() if consensus_text is not None else None,
        "consensus_markdown": consensus_text,
        "packet": raw["packet.md"],
        "instructions": raw["instructions.md"],
        "pathway_log_entry": raw["pathway-log-entry.md"],
    }


def _str_list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def load_portfolios(rm_root: Path) -> list[Portfolio]:
    """Portfolios from RM ``config/fleet_manifest.yaml`` in file order; malformed entries are skipped."""
    path = rm_root / MANIFEST_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SourceUnavailable(f"fleet manifest unreadable: {type(exc).__name__}") from exc
    raw = data.get("portfolios") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise SourceUnavailable("fleet manifest has no portfolios mapping")
    portfolios: list[Portfolio] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            log.warning("priorities: portfolio %r is not a mapping; skipped", name)
            continue
        wip = spec.get("wip_limit")
        portfolios.append(
            Portfolio(
                name=str(name),
                description=str(spec.get("description") or ""),
                wip_limit=wip if isinstance(wip, int) and not isinstance(wip, bool) and wip >= 0 else None,
                review_cadence=str(spec.get("review_cadence") or ""),
                unattended_agents=_str_list(spec.get("unattended_agents")),
                repos=_str_list(spec.get("repos")),
            )
        )
    return portfolios
