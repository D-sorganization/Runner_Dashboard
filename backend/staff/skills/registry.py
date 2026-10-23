"""Skill index from Repository_Management ``skills/`` (epic #2157 slice 1)."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from staff.skills.contract import SkillDetail, SkillSummary, SkillValidationError, parse_skill_md, semver_sort_key
from staff.skills.pointer import resolve_skill_source
from staff.workspace import rm_root

log = logging.getLogger("dashboard.staff.skills")
SKILLS_DIR_ENV = "STAFF_SKILLS_DIR"
_SKILL_GLOB = "*/*/SKILL.md"

_CACHE_LOCK = threading.Lock()
_CACHE_DIR_SIG: tuple[str, tuple[tuple[str, int, int], ...]] | None = None
_CACHE_SUMMARIES: dict[str, SkillSummary] = {}
_CACHE_DETAILS: dict[tuple[str, str], SkillDetail] = {}
_CACHE_ERRORS: dict[str, list[str]] = {}


def skills_dir() -> Path | None:
    override = os.environ.get(SKILLS_DIR_ENV)
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    root = rm_root()
    if root is not None:
        cand = root / "skills"
        if cand.is_dir():
            return cand
    here = Path(__file__).resolve()
    rel = ("Repository_Management", "skills")
    candidates = [
        here.parents[3].parent.joinpath(*rel),
        here.parents[3].joinpath(*rel),
        Path.home() / "Repositories" / "Repository_Management" / "skills",
    ]
    return next((c for c in candidates if c.is_dir()), None)


def clear_skills_cache() -> None:
    global _CACHE_DIR_SIG, _CACHE_SUMMARIES, _CACHE_DETAILS, _CACHE_ERRORS
    with _CACHE_LOCK:
        _CACHE_DIR_SIG = None
        _CACHE_SUMMARIES = {}
        _CACHE_DETAILS = {}
        _CACHE_ERRORS = {}


def _load_directory(root: Path) -> None:
    global _CACHE_SUMMARIES, _CACHE_DETAILS, _CACHE_ERRORS
    summaries: dict[str, SkillSummary] = {}
    details: dict[tuple[str, str], SkillDetail] = {}
    errors: dict[str, list[str]] = {}
    for skill_file in sorted(root.glob(_SKILL_GLOB)):
        rel = str(skill_file.relative_to(root))
        try:
            body_text, package_dir, _ = resolve_skill_source(skill_file)
            detail = parse_skill_md(
                body_text,
                source_path=rel,
                package_dir=package_dir,
            )
        except (OSError, SkillValidationError) as exc:
            errors[rel] = [str(exc)]
            continue
        key = (detail.name, detail.version)
        if key in details:
            errors[rel] = [f"duplicate skill name/version {detail.pin}"]
            continue
        details[key] = detail
        prev = summaries.get(detail.name)
        if prev is None or semver_sort_key(detail.version) > semver_sort_key(prev.version):
            summaries[detail.name] = SkillSummary(**{f: getattr(detail, f) for f in SkillSummary.__dataclass_fields__})
    _CACHE_SUMMARIES, _CACHE_DETAILS, _CACHE_ERRORS = summaries, details, errors


def _ensure_loaded() -> None:
    global _CACHE_DIR_SIG
    root = skills_dir()
    if root is None:
        with _CACHE_LOCK:
            _CACHE_DIR_SIG = ("", ())
            _CACHE_SUMMARIES.clear()
            _CACHE_DETAILS.clear()
            _CACHE_ERRORS.clear()
        return
    file_stats: list[tuple[str, int, int]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            try:
                st = path.stat()
                file_stats.append((str(path.relative_to(root)), st.st_mtime_ns, st.st_size))
            except OSError:
                file_stats.append((str(path.relative_to(root)), -1, -1))
    sig = (str(root.resolve()), tuple(file_stats))
    with _CACHE_LOCK:
        if _CACHE_DIR_SIG == sig:
            return
        _CACHE_DIR_SIG = sig
        _load_directory(root)


def skill_validation_errors() -> dict[str, list[str]]:
    _ensure_loaded()
    with _CACHE_LOCK:
        return dict(_CACHE_ERRORS)


def repo_matches_skill(skill_repos: tuple[str, ...], repo: str) -> bool:
    if not skill_repos or "*" in skill_repos:
        return True
    return bool(repo) and repo in skill_repos


def list_skills(
    *,
    role: str | None = None,
    repo: str | None = None,
    task_type: str | None = None,
) -> list[SkillSummary]:
    _ensure_loaded()
    with _CACHE_LOCK:
        rows = list(_CACHE_SUMMARIES.values())
    if role:
        rows = [r for r in rows if role in r.roles]
    if repo:
        rows = [r for r in rows if repo_matches_skill(r.repos, repo)]
    if task_type:
        rows = [r for r in rows if task_type in r.task_types]
    return sorted(rows, key=lambda r: r.name)


def get_skill(name: str, version: str | None = None) -> SkillDetail | None:
    _ensure_loaded()
    with _CACHE_LOCK:
        if version is not None:
            return _CACHE_DETAILS.get((name, version))
        summary = _CACHE_SUMMARIES.get(name)
        return _CACHE_DETAILS.get((name, summary.version)) if summary else None
