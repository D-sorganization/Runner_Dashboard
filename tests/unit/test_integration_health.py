"""Unit tests for integration_health (#3180 standalone/no-fleet profile detection)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from integration_health import (  # noqa: E402
    NO_RM_REASON,
    STANDALONE_RM_REASON,
    check_all_integration_availability,
    check_repository_management,
)
from staff import roles as roles_mod  # noqa: E402
from staff import workspace  # noqa: E402
from staff.skills import registry as skills_registry  # noqa: E402


def test_check_repository_management_unavailable_without_rm_root(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DASHBOARD_STANDALONE", raising=False)
    monkeypatch.delenv("STAFF_STANDALONE", raising=False)
    monkeypatch.delenv("STAFF_RM_ROOT", raising=False)
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path))
    monkeypatch.setattr(workspace, "_default_repos_roots", lambda _home: [])

    result = check_repository_management()

    assert result == {"available": False, "reason": NO_RM_REASON}


def test_check_repository_management_available_with_rm_root_override(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DASHBOARD_STANDALONE", raising=False)
    monkeypatch.delenv("STAFF_STANDALONE", raising=False)
    rm_dir = tmp_path / "Repository_Management"
    scripts_dir = rm_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "check_agent_claim.py").write_text("# marker\n")
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm_dir))

    result = check_repository_management()

    assert result == {"available": True, "reason": "", "path": str(rm_dir)}


def test_check_repository_management_unavailable_with_bad_rm_root_override(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DASHBOARD_STANDALONE", raising=False)
    monkeypatch.delenv("STAFF_STANDALONE", raising=False)
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "does-not-exist"))

    result = check_repository_management()

    assert result == {"available": False, "reason": NO_RM_REASON}


def test_standalone_flag_disables_rm_even_when_checkout_present(monkeypatch, tmp_path) -> None:
    """DASHBOARD_STANDALONE=1 forces standalone profile even if STAFF_RM_ROOT points at a valid checkout."""
    rm_dir = tmp_path / "Repository_Management"
    (rm_dir / "scripts").mkdir(parents=True)
    (rm_dir / "scripts" / "check_agent_claim.py").write_text("# marker\n")
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm_dir))
    monkeypatch.setenv("DASHBOARD_STANDALONE", "1")

    assert workspace.is_standalone_mode() is True
    assert workspace.rm_root() is None
    assert check_repository_management() == {
        "available": False,
        "reason": STANDALONE_RM_REASON,
    }


def test_standalone_flag_gates_roles_and_skills(monkeypatch, tmp_path) -> None:
    """In standalone mode, sibling RM roles and skills are suppressed and fall back cleanly."""
    rm_dir = tmp_path / "Repository_Management"
    (rm_dir / "scripts").mkdir(parents=True)
    (rm_dir / "scripts" / "check_agent_claim.py").write_text("# marker\n")
    (rm_dir / "staff" / "roles").mkdir(parents=True)
    (rm_dir / "skills").mkdir(parents=True)
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm_dir))
    monkeypatch.delenv("STAFF_ROLES_DIR", raising=False)
    monkeypatch.delenv("STAFF_SKILLS_DIR", raising=False)
    monkeypatch.delenv("RD_OVERLAY_PATH", raising=False)
    monkeypatch.setenv("STAFF_STANDALONE", "true")

    roles_mod.clear_roles_cache()
    skills_registry.clear_skills_cache()

    assert roles_mod.roles_dir() is None
    assert list(roles_mod.load_roles()) == [roles_mod.AD_HOC_ROLE]
    assert skills_registry.skills_dir() is None
    assert skills_registry.list_skills() == []


def test_check_all_integration_availability_shape(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DASHBOARD_STANDALONE", raising=False)
    monkeypatch.delenv("STAFF_STANDALONE", raising=False)
    monkeypatch.delenv("STAFF_RM_ROOT", raising=False)
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path))
    monkeypatch.setattr(workspace, "_default_repos_roots", lambda _home: [])

    result = check_all_integration_availability()

    assert set(result) == {"repository_management"}
    assert result["repository_management"]["available"] is False
    assert result["repository_management"]["reason"] == NO_RM_REASON


def test_check_all_integration_availability_reports_available(monkeypatch, tmp_path) -> None:
    rm_dir = tmp_path / "Repository_Management"
    scripts_dir = rm_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "check_agent_claim.py").write_text("# marker\n")
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm_dir))

    result = check_all_integration_availability()

    assert result["repository_management"]["available"] is True
