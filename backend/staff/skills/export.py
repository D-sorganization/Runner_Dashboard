"""Export indexed skills to provider-native layouts (#2157 slice 4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from staff.skills.contract import SkillDetail, SkillSummary


@dataclass(frozen=True)
class SkillExportFile:
    relative_path: str
    content: str


def _frontmatter(skill: SkillSummary) -> str:
    lines = [
        "---",
        f"name: {skill.name}",
        f"version: {skill.version}",
        f"pin: {skill.pin}",
    ]
    if skill.description:
        lines.append(f"description: {skill.description}")
    if skill.when_to_use:
        lines.append(f"when_to_use: {skill.when_to_use}")
    lines.extend(
        [
            f"roles: [{', '.join(skill.roles)}]",
            f"repos: [{', '.join(skill.repos)}]",
            f"task_types: [{', '.join(skill.task_types)}]",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def export_skill_markdown(detail: SkillDetail) -> str:
    """Cursor / Claude Code skill file body."""
    parts = [_frontmatter(detail), detail.body.strip()]
    if detail.attachments:
        parts.append("\n## Bundled attachments\n")
        for att in detail.attachments:
            parts.append(f"### {att.path} ({att.kind})\n\n{att.content.strip()}\n")
    return "\n".join(parts).strip() + "\n"


def export_codex_agents_section(skills: list[SkillDetail]) -> str:
    """Markdown section appended to AGENTS.md / Codex context."""
    if not skills:
        return ""
    lines = ["## Fleet skills (session export)", ""]
    for skill in skills:
        lines.append(f"### {skill.name} ({skill.pin})")
        if skill.when_to_use or skill.description:
            lines.append(skill.when_to_use or skill.description)
        lines.append("")
        lines.append(skill.body.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_exports(
    skills: list[SkillDetail],
    dest: Path,
    *,
    provider: str,
) -> list[SkillExportFile]:
    """Write exports under ``dest``; return the files written."""
    dest.mkdir(parents=True, exist_ok=True)
    written: list[SkillExportFile] = []
    provider_key = provider.strip().lower() or "generic"
    if provider_key in ("codex", "gemini"):
        rel = "AGENTS.skills.md"
        content = export_codex_agents_section(skills)
        path = dest / rel
        path.write_text(content, encoding="utf-8")
        written.append(SkillExportFile(rel, content))
        return written
    sub = dest / "skills"
    sub.mkdir(parents=True, exist_ok=True)
    for skill in skills:
        rel = f"skills/{skill.name}.md"
        content = export_skill_markdown(skill)
        (dest / rel).write_text(content, encoding="utf-8")
        written.append(SkillExportFile(rel, content))
    return written
