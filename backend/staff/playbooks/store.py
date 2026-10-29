"""Playbook/manual directory loader — list and render source for the API (#3488).

Mirrors the resolution shape of ``staff/roles.py``: an operator override env
var, then a sibling Repository_Management checkout, else nothing (an empty
catalog rather than a hard failure, matching that module's fallback stance).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from staff.playbooks.schema import (
    BarbOperatingManual,
    PlaybookDefinition,
    PlaybookValidationError,
    parse_operating_manual,
    parse_playbook,
)

PLAYBOOKS_DIR_ENV = "STAFF_PLAYBOOKS_DIR"
_SIBLING_RELATIVE = ("Repository_Management", "staff", "playbooks")
_MANUAL_FILENAMES = ("manual.yml", "manual.yaml")

_CACHE_LOCK = threading.Lock()
_CACHE_SIG: tuple[str, tuple[tuple[str, int], ...]] | None = None
_CACHE_PLAYBOOKS: dict[str, PlaybookDefinition] = {}
_CACHE_MANUAL: BarbOperatingManual | None = None
_CACHE_ERRORS: dict[str, str] = {}


def playbooks_dir() -> Path | None:
    """Resolve the playbooks directory. Returns None when nothing is found."""
    override = os.environ.get(PLAYBOOKS_DIR_ENV)
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    here = Path(__file__).resolve()
    for parent in (here.parents[2], here.parents[3]) if len(here.parents) > 3 else (here.parents[2],):
        for candidate in (parent.parent.joinpath(*_SIBLING_RELATIVE), parent.joinpath(*_SIBLING_RELATIVE)):
            if candidate.is_dir():
                return candidate
    return None


def clear_playbooks_cache() -> None:
    """Clear the in-memory playbook/manual cache (used by tests)."""
    global _CACHE_SIG, _CACHE_PLAYBOOKS, _CACHE_MANUAL, _CACHE_ERRORS
    with _CACHE_LOCK:
        _CACHE_SIG = None
        _CACHE_PLAYBOOKS = {}
        _CACHE_MANUAL = None
        _CACHE_ERRORS = {}


def _load(directory: Path | None) -> None:
    global _CACHE_SIG, _CACHE_PLAYBOOKS, _CACHE_MANUAL, _CACHE_ERRORS
    if directory is None:
        sig: tuple[str, tuple[tuple[str, int], ...]] = ("", ())
        with _CACHE_LOCK:
            if _CACHE_SIG != sig:
                _CACHE_SIG, _CACHE_PLAYBOOKS, _CACHE_MANUAL, _CACHE_ERRORS = sig, {}, None, {}
        return

    files = sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml"))
    file_stats: list[tuple[str, int]] = []
    for p in files:
        try:
            file_stats.append((p.name, p.stat().st_mtime_ns))
        except OSError:
            file_stats.append((p.name, -1))
    sig = (str(directory.resolve()), tuple(file_stats))

    with _CACHE_LOCK:
        if _CACHE_SIG == sig:
            return
        manual: BarbOperatingManual | None = None
        playbooks: dict[str, PlaybookDefinition] = {}
        errors: dict[str, str] = {}
        for path in files:
            raw = path.read_text(encoding="utf-8")
            if path.name in _MANUAL_FILENAMES:
                try:
                    manual = parse_operating_manual(raw)
                except PlaybookValidationError as exc:
                    errors[path.name] = str(exc)
                continue
            try:
                pb = parse_playbook(raw)
            except PlaybookValidationError as exc:
                errors[path.name] = str(exc)
                continue
            playbooks[pb.playbook_id] = pb
        _CACHE_SIG, _CACHE_PLAYBOOKS, _CACHE_MANUAL, _CACHE_ERRORS = sig, playbooks, manual, errors


def load_playbooks() -> dict[str, PlaybookDefinition]:
    """Load every playbook keyed by ``playbook_id`` (manual.yml excluded)."""
    _load(playbooks_dir())
    with _CACHE_LOCK:
        return dict(_CACHE_PLAYBOOKS)


def load_manual() -> BarbOperatingManual | None:
    """Load the Barb Operating Manual, or ``None`` if no manual.yml is published."""
    _load(playbooks_dir())
    with _CACHE_LOCK:
        return _CACHE_MANUAL


def playbook_load_errors() -> dict[str, str]:
    """Return map of file name -> parse error, for files that failed to load."""
    _load(playbooks_dir())
    with _CACHE_LOCK:
        return dict(_CACHE_ERRORS)
