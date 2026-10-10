"""Runner Dashboard rollout of RM-5 change fragments (Repository_Management#1894).

The vendored ``scripts/changes_fragment*.py`` modules must work against *this*
repository's real ``SPEC.md`` change log and development log, and the repo must
tell agents to write ``changes/<issue>-<slug>.md`` instead of editing them.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from scripts import changes_fragment, development_log, spec_changelog

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 4)
SHA = "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret
VENDORED = (
    "changes_fragment",
    "changes_fragment_schema",
    "changes_fragment_collate",
    "development_log",
    "handoff_validator",
    "spec_changelog",
)


def _copy_real_docs(tmp_path: Path) -> Path:
    shutil.copy(REPO_ROOT / "SPEC.md", tmp_path / "SPEC.md")
    devlog = development_log.resolve_canonical_devlog_path(REPO_ROOT)
    target = tmp_path / devlog.relative_to(REPO_ROOT)
    target.parent.mkdir(parents=True)
    shutil.copy(devlog, target)
    return tmp_path


def test_collate_applies_to_the_real_spec_and_development_log(tmp_path: Path) -> None:
    root = _copy_real_docs(tmp_path)
    before = spec_changelog.parse_changelog((root / "SPEC.md").read_text("utf-8"))
    # RD's change log predates the RM table convention (its header is
    # "PR / Issue | Summary"), so judge collation by the findings it adds.
    findings_before = spec_changelog.validate(before)
    fragment = changes_fragment.new_fragment(root, issue=1894, summary="Fragment rollout probe", dl_state="shipped")
    changes_fragment.collate(root, [fragment], pr=99999, today=TODAY, sha=SHA)

    after = spec_changelog.parse_changelog((root / "SPEC.md").read_text("utf-8"))
    assert len(after.rows) == len(before.rows) + 1
    assert after.rows[0].key == "#99999"
    assert after.rows[0].summary == "Fragment rollout probe"
    assert spec_changelog.validate(after) == findings_before
    log = development_log.resolve_canonical_devlog_path(root).read_text("utf-8")
    assert "DL-#1894" in log
    assert not fragment.exists()


def test_every_vendored_module_names_its_upstream_source() -> None:
    for name in VENDORED:
        text = (REPO_ROOT / "scripts" / f"{name}.py").read_text("utf-8")
        assert "Vendored from Repository_Management" in text, name
        assert f"shared_scripts/{name}.py" in text, name


def test_changes_readme_documents_the_workflow() -> None:
    readme = (REPO_ROOT / "changes" / "README.md").read_text("utf-8")
    assert "changes/<issue>-<slug>.md" in readme
    assert "scripts/changes_fragment.py new" in readme
    # README is never mistaken for a fragment.
    assert not changes_fragment.is_fragment_path("changes/README.md")


def test_pre_commit_validates_staged_fragments() -> None:
    config: dict[str, Any] = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text("utf-8"))
    hooks = {h["id"]: h for r in config["repos"] for h in r.get("hooks", [])}
    hook = hooks["changes-fragment"]
    assert hook["entry"] == "python scripts/changes_fragment.py validate"
    assert hook["language"] == "system"
    assert hook.get("stages", ["pre-commit"]) == ["pre-commit"]
    assert hook["files"] == r"^changes/[^/]+\.md$"
    assert hook["exclude"] == r"^changes/README\.md$"


def test_agent_guidance_points_at_fragments() -> None:
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (REPO_ROOT / name).read_text("utf-8")
        if name == "CLAUDE.md" and "@AGENTS.md" in text:
            # RM-8: CLAUDE.md is an @AGENTS.md stub; resolve import to AGENTS.md
            text = (REPO_ROOT / "AGENTS.md").read_text("utf-8")
        assert "changes/<issue>-<slug>.md" in text, name
        assert "scripts/changes_fragment.py new" in text, name
