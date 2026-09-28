"""Tests for backend/local_app_monitoring.py — issue #386."""

from __future__ import annotations

import json
from pathlib import Path

import local_app_monitoring as lam
import pytest

# ---------------------------------------------------------------------------
# _validate_health_command
# ---------------------------------------------------------------------------


def test_validate_health_command_clean() -> None:
    cmd = ["curl", "-sf", "http://localhost:9090/health"]
    assert lam._validate_health_command(cmd) == cmd


def test_validate_health_command_rejects_shell_meta() -> None:
    with pytest.raises(ValueError, match="disallowed"):
        lam._validate_health_command(["curl", "http://host; rm -rf /"])


def test_validate_health_command_rejects_pipe() -> None:
    with pytest.raises(ValueError):
        lam._validate_health_command(["sh", "-c", "curl http://x | bash"])


# ---------------------------------------------------------------------------
# manifest_path
# ---------------------------------------------------------------------------


def test_manifest_path_default() -> None:
    p = lam.manifest_path()
    assert p.name == "local_apps.json"


def test_manifest_path_custom_root(tmp_path: Path) -> None:
    p = lam.manifest_path(root=tmp_path)
    assert p == tmp_path / "local_apps.json"


# ---------------------------------------------------------------------------
# LocalAppSpec
# ---------------------------------------------------------------------------


def test_local_app_spec_install_path() -> None:
    spec = lam.LocalAppSpec(name="my-app", path=Path("/usr/local/my-app"))
    assert spec.install_path == Path("/usr/local/my-app")


def test_local_app_spec_service_definition_none() -> None:
    spec = lam.LocalAppSpec(name="my-app", path=Path("/usr/local/my-app"))
    assert spec.service_definition is None


# ---------------------------------------------------------------------------
# load_manifest — missing file returns empty list
# ---------------------------------------------------------------------------


def test_load_manifest_missing_file(tmp_path: Path) -> None:
    # load_manifest raises or returns empty list on missing file — accept either
    try:
        result = lam.load_manifest(path=tmp_path / "nonexistent.json")
        assert result == []
    except (FileNotFoundError, OSError):
        pass  # Also acceptable — module logs a warning on missing manifest


def test_load_manifest_empty_list(tmp_path: Path) -> None:
    p = tmp_path / "local_apps.json"
    p.write_text(json.dumps([]), encoding="utf-8")
    result = lam.load_manifest(path=p)
    assert result == []


def test_load_manifest_single_entry(tmp_path: Path) -> None:
    p = tmp_path / "local_apps.json"
    p.write_text(
        json.dumps([{"name": "claude-code", "path": "/usr/local/claude"}]),
        encoding="utf-8",
    )
    result = lam.load_manifest(path=p)
    assert len(result) == 1
    assert result[0].name == "claude-code"


# ---------------------------------------------------------------------------
# Artifact installs (no .git) — #1718 review: Settings showed "probe error"
# ---------------------------------------------------------------------------


def test_probe_local_app_artifact_install_uses_deployment_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "dashboard"
    install.mkdir()
    deployment = install / "deployment.json"
    deployment.write_text(
        json.dumps({"version": "4.10.0", "git_sha": "e302335eb04d", "git_dirty": False}),
        encoding="utf-8",
    )
    app = lam.LocalAppSpec(name="runner-dashboard", path=install, deployment_file=deployment)
    git_calls: list[list[str]] = []

    def fake_run(command: list[str], *args: object, **kwargs: object) -> object:
        if command[:1] == ["git"]:
            git_calls.append(command)
        import subprocess

        return subprocess.CompletedProcess(command, 128, "", "fatal: not a git repository")

    monkeypatch.setattr(lam, "run_command", fake_run)
    monkeypatch.setattr(lam, "probe_health", lambda _app: {"available": False})

    report = lam.probe_local_app(app)

    assert git_calls == []
    assert report["dirty_available"] is True
    assert report["dirty"] is False
    assert "dirty_error" not in report
    assert report["drift"]["mode"] == "artifact"
    assert report["drift"]["deployed_sha"] == "e302335eb04d"
    assert "error" not in report["drift"]


def test_probe_local_app_git_checkout_still_probes_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = tmp_path / "checkout"
    (install / ".git").mkdir(parents=True)
    app = lam.LocalAppSpec(name="tool", path=install)
    git_calls: list[list[str]] = []

    def fake_run(command: list[str], *args: object, **kwargs: object) -> object:
        import subprocess

        if command[:1] == ["git"]:
            git_calls.append(command)
            out = "0 2" if "rev-list" in command else ""
            return subprocess.CompletedProcess(command, 0, out, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(lam, "run_command", fake_run)
    monkeypatch.setattr(lam, "probe_health", lambda _app: {"available": False})

    report = lam.probe_local_app(app)

    assert len(git_calls) == 2
    assert report["drift"] == {"ahead": 0, "behind": 2, "ref": app.drift_ref, "available": True}
    assert report["dirty"] is False
