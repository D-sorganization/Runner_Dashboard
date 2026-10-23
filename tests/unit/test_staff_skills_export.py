"""Skill export formats (#2157 slice 4)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.skills.contract import SkillAttachment, SkillDetail  # noqa: E402
from staff.skills.delivery import deliver_skills_for_role  # noqa: E402
from staff.skills.export import export_codex_agents_section, export_skill_markdown, write_exports  # noqa: E402
from staff.skills.registry import clear_skills_cache  # noqa: E402

_SAMPLE = SkillDetail(
    name="fleet-open-pr",
    version="1.0.0",
    description="Open a PR",
    when_to_use="",
    roles=("barb",),
    repos=("Runner_Dashboard",),
    task_types=("open-pr",),
    content_hash="abc",
    pin="fleet-open-pr@1.0.0+abc",
    source_path="fleet/open-pr/SKILL.md",
    package_dir="",
    body="Use fragments.",
    attachments=(
        SkillAttachment(path="templates/pr.md", kind="template", content="# PR", content_hash="def"),
    ),
)


def test_export_markdown_includes_body_and_attachment() -> None:
    text = export_skill_markdown(_SAMPLE)
    assert "fleet-open-pr@1.0.0+abc" in text
    assert "Use fragments." in text
    assert "templates/pr.md" in text


def test_codex_section_lists_skills() -> None:
    section = export_codex_agents_section([_SAMPLE])
    assert "Fleet skills" in section
    assert "fleet-open-pr" in section


def test_write_exports_cursor_layout(tmp_path: Path) -> None:
    files = write_exports([_SAMPLE], tmp_path, provider="cursor")
    assert any(f.relative_path == "skills/fleet-open-pr.md" for f in files)
    assert (tmp_path / "skills" / "fleet-open-pr.md").is_file()


def test_deliver_skills_for_role_uses_registry(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    clear_skills_cache()
    root = tmp_path / "skills" / "fleet" / "open-pr"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: fleet-open-pr\nversion: 1.0.0\ndescription: x\nroles: [barb]\n"
        "repos: [Runner_Dashboard]\ntask_types: [open-pr]\n---\nBody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("RUNNER_DASHBOARD_SKILLS_EXPORT_DIR", str(tmp_path / "out"))
    summary = deliver_skills_for_role("barb", provider="cursor")
    assert summary["exported"] == 1
    assert (tmp_path / "out" / "cursor" / "skills" / "fleet-open-pr.md").is_file()
    clear_skills_cache()
