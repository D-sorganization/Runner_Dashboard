"""Skill package contract: ``SKILL.md`` frontmatter and attachment metadata (#2157)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_SEMVER_PARSE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_ATTACHMENT_KINDS = frozenset({"template", "requirements", "fixture", "other"})


class SkillValidationError(ValueError):
    """A skill package violates the v1 contract."""


@dataclass(frozen=True)
class SkillAttachment:
    path: str
    kind: str
    content: str
    content_hash: str


@dataclass(frozen=True)
class SkillSummary:
    name: str
    version: str
    description: str
    when_to_use: str
    roles: tuple[str, ...]
    repos: tuple[str, ...]
    task_types: tuple[str, ...]
    content_hash: str
    pin: str
    source_path: str
    package_dir: str


@dataclass(frozen=True)
class SkillDetail(SkillSummary):
    body: str
    attachments: tuple[SkillAttachment, ...]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def semver_sort_key(version: str) -> tuple[int, int, int, int, str]:
    """Precedence key for SemVer 2.0 (stable releases beat prereleases)."""
    match = _SEMVER_PARSE.match(version.strip())
    if not match:
        return (0, 0, 0, 0, version)
    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    prerelease = match.group(4)
    stable_rank = 1 if prerelease is None else 0
    return (major, minor, patch, stable_rank, prerelease or "")


def _as_str_tuple(value: Any, field: str, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        problems.append(f"{field} is required")
        return ()
    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, list | tuple):
        out = tuple(str(v).strip() for v in value if str(v).strip())
        if not out:
            problems.append(f"{field} must be a non-empty list")
        return out
    problems.append(f"{field} must be a list of strings")
    return ()


def parse_skill_md(text: str, *, source_path: str, package_dir: Path | None = None) -> SkillDetail:
    """Parse ``SKILL.md``; raise ``SkillValidationError`` when the v1 contract is violated."""
    problems: list[str] = []
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        raise SkillValidationError("SKILL.md must begin with YAML frontmatter")
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        raise SkillValidationError("SKILL.md frontmatter is not closed")
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"frontmatter YAML invalid: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillValidationError("frontmatter must be a mapping")
    body = parts[2].lstrip("\n")

    name = str(meta.get("name") or "").strip()
    if not name and source_path:
        # skills/<repo>/<slug>/SKILL.md → default name from slug
        slug = Path(source_path).parent.name
        name = slug.replace("_", "-")
    if not name or not _NAME_RE.match(name):
        problems.append("name must be a kebab-case identifier")

    version = str(meta.get("version") or "").strip()
    if not version or not _SEMVER_RE.match(version):
        problems.append("version must be semver (e.g. 1.0.0)")

    description = str(meta.get("description") or "").strip()
    when_to_use = str(meta.get("when_to_use") or meta.get("when-to-use") or "").strip()
    if not description and not when_to_use:
        problems.append("description or when_to_use is required")

    roles = _as_str_tuple(meta.get("roles"), "roles", problems)
    repos = _as_str_tuple(meta.get("repos"), "repos", problems)
    task_types = _as_str_tuple(meta.get("task_types") or meta.get("task-types"), "task_types", problems)
    raw_attachments = meta.get("attachments")
    if raw_attachments is not None and not isinstance(raw_attachments, list):
        problems.append("attachments must be a list")
    if problems:
        raise SkillValidationError("; ".join(problems))

    content_hash = _sha256_text(stripped)
    pin = f"{name}@{version}+{content_hash[:12]}"
    pkg_dir = package_dir or Path(source_path).parent
    attachments: list[SkillAttachment] = []
    for idx, item in enumerate(raw_attachments or []):
        if not isinstance(item, dict):
            raise SkillValidationError(f"attachments[{idx}] must be a mapping")
        path = str(item.get("path") or "").strip()
        kind = str(item.get("kind") or "other").strip().lower()
        if not path or ".." in Path(path).parts or path.startswith("/"):
            raise SkillValidationError(f"attachments[{idx}].path is invalid")
        if kind not in _ATTACHMENT_KINDS:
            raise SkillValidationError(f"attachments[{idx}].kind must be one of {sorted(_ATTACHMENT_KINDS)}")
        file_path = (pkg_dir / path).resolve()
        try:
            file_path.relative_to(pkg_dir.resolve())
        except ValueError:
            raise SkillValidationError(f"attachment path escapes package: {path}") from None
        try:
            att_text = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillValidationError(f"attachment missing or unreadable: {path} ({exc})") from exc
        attachments.append(SkillAttachment(path=path, kind=kind, content=att_text, content_hash=_sha256_text(att_text)))

    summary = SkillSummary(
        name=name,
        version=version,
        description=description,
        when_to_use=when_to_use,
        roles=roles,
        repos=repos,
        task_types=task_types,
        content_hash=content_hash,
        pin=pin,
        source_path=source_path,
        package_dir=str(pkg_dir),
    )
    return SkillDetail(**summary.__dict__, body=body, attachments=tuple(attachments))
