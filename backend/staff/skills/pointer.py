"""Resolve skill packages that point at another repo checkout (#2157 slice 3)."""

from __future__ import annotations

from pathlib import Path

import yaml
from staff.skills.contract import SkillValidationError
from staff.workspace import find_repo_checkout


def _parse_frontmatter(text: str) -> dict[str, object]:
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}
    meta = yaml.safe_load(parts[1])
    return meta if isinstance(meta, dict) else {}


def _repo_name(pointer: dict[str, object]) -> str:
    raw = pointer.get("repo") or pointer.get("repository") or pointer.get("github") or ""
    name = str(raw).strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def resolve_skill_source(skill_file: Path) -> tuple[str, Path, str]:
    """Load ``SKILL.md`` text and attachment root, following an optional ``pointer`` block.

    Post: ``package_dir`` is the directory holding attachment paths; ``logical_path`` is the
    registry-relative path of the stub (or the skill file itself when not a pointer).
    """
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillValidationError(f"cannot read {skill_file}: {exc}") from exc
    meta = _parse_frontmatter(text)
    pointer = meta.get("pointer")
    if not isinstance(pointer, dict):
        return text, skill_file.parent, str(skill_file.name)

    repo = _repo_name(pointer)
    rel = str(pointer.get("path") or "").strip()
    if not repo or not rel or ".." in Path(rel).parts or rel.startswith("/"):
        raise SkillValidationError("pointer must include repo and a relative path")

    checkout = find_repo_checkout(repo)
    if checkout is None:
        raise SkillValidationError(f"pointer repo checkout not found: {repo!r}")

    target = (checkout / rel).resolve()
    try:
        target.relative_to(checkout.resolve())
    except ValueError:
        raise SkillValidationError(f"pointer path escapes checkout: {rel}") from None
    if not target.is_file():
        raise SkillValidationError(f"pointer target missing: {repo}/{rel}")
    try:
        resolved = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillValidationError(f"cannot read pointer target {repo}/{rel}: {exc}") from exc
    return resolved, target.parent, str(skill_file.name)
