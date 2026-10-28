"""Unit tests for integration_health (#3180 standalone/no-fleet profile detection)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from integration_health import check_all_integration_availability, check_repository_management  # noqa: E402
from priorities.service import NO_RM_REASON  # noqa: E402


def test_check_repository_management_unavailable_without_rm_root(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("STAFF_RM_ROOT", raising=False)
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path))

    result = check_repository_management()

    assert result == {"available": False, "reason": NO_RM_REASON}


def test_check_repository_management_available_with_rm_root_override(monkeypatch, tmp_path) -> None:
    rm_dir = tmp_path / "Repository_Management"
    scripts_dir = rm_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "check_agent_claim.py").write_text("# marker\n")
    monkeypatch.setenv("STAFF_RM_ROOT", str(rm_dir))

    result = check_repository_management()

    assert result == {"available": True, "reason": "", "path": str(rm_dir)}


def test_check_repository_management_unavailable_with_bad_rm_root_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "does-not-exist"))

    result = check_repository_management()

    assert result == {"available": False, "reason": NO_RM_REASON}


def test_check_all_integration_availability_shape(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("STAFF_RM_ROOT", raising=False)
    monkeypatch.setenv("STAFF_REPOS_ROOT", str(tmp_path))

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
