"""Keep docs/upgrade-rollback.md honest about what upgrade/rollback actually do (#3725)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "upgrade-rollback.md"
LIB = ROOT / "deploy" / "lib.sh"
UPDATE_SCRIPT = ROOT / "deploy" / "update-deployed.sh"
ROLLBACK_SCRIPT = ROOT / "deploy" / "rollback.sh"
ROLLBACK_DEPLOYED_SCRIPT = ROOT / "deploy" / "rollback-deployed.sh"


def test_doc_exists() -> None:
    assert DOC.is_file(), "docs/upgrade-rollback.md must exist (#3725)"


def test_doc_describes_the_real_backup_integrity_manifest() -> None:
    text = DOC.read_text(encoding="utf-8")
    lib_text = LIB.read_text(encoding="utf-8")
    assert "manifest.sha256" in text
    assert "manifest.sha256" in lib_text, (
        "deploy/lib.sh no longer writes a .manifest.sha256 integrity manifest; "
        "docs/upgrade-rollback.md's description is stale"
    )
    assert "backup_dir" in lib_text


def test_doc_describes_the_real_fail_closed_and_non_fail_closed_rollback_paths() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    rollback_text = ROLLBACK_SCRIPT.read_text(encoding="utf-8")
    # rollback.sh aborts on a verified integrity failure (fail-closed)...
    assert "integrity check failed" in rollback_text.lower()
    assert "fail-closed" in text or "fail closed" in text
    # ...but only warns and proceeds when a backup predates manifest tracking.
    assert "missing checksum manifest" in rollback_text.lower()
    assert "without integrity tracking" in text or "missing checksum manifest" in text or "pre-manifest" in text


def test_doc_flags_the_update_failure_rollback_gap() -> None:
    """update-deployed.sh backs up with a manifest but auto-rollback-on-failure
    calls rollback-deployed.sh, which restores via rsync with no manifest
    verification. The doc must say so honestly rather than imply both rollback
    paths are integrity-verified."""
    update_text = UPDATE_SCRIPT.read_text(encoding="utf-8")
    rollback_deployed_text = ROLLBACK_DEPLOYED_SCRIPT.read_text(encoding="utf-8")
    assert "rollback-deployed.sh" in update_text
    assert "manifest" not in rollback_deployed_text.lower(), (
        "deploy/rollback-deployed.sh now checks a manifest; "
        "docs/upgrade-rollback.md's 'no verification on this path' claim is stale"
    )
    text = DOC.read_text(encoding="utf-8")
    assert "#3725" in text
    assert "rollback-deployed.sh" in text
    assert "rollback.sh" in text


def test_doc_describes_db_migration_rollback_guarantee() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    rollback_text = ROLLBACK_SCRIPT.read_text(encoding="utf-8").lower()
    assert "alembic" in text
    assert "alembic downgrade" in rollback_text
    assert "db migration rollback failed" in rollback_text
