"""Regression for #2501: host provider CLIs / OAuth seats must not leak into tests."""

from __future__ import annotations

import pytest
from staff import cli_version


@pytest.mark.unit
@pytest.mark.parametrize("name", ["claude", "agy", "codex", "antigravity"])
def test_provider_clis_are_not_discoverable(name: str) -> None:
    assert cli_version.shutil.which(name) is None


@pytest.mark.unit
def test_auth_probe_ignores_host_oauth_state() -> None:
    from staff.provider_auth_preflight import probe_local_provider_auth  # noqa: PLC0415

    rows = probe_local_provider_auth("Desk")["providers"]
    assert {row["status"] for row in rows.values()} == {"ok"}


@pytest.mark.unit
def test_staff_cli_installed_fake_version_is_a_comparable_tuple(staff_cli_installed) -> None:
    staff_cli_installed("claude")
    version = cli_version.installed_version("claude")
    assert isinstance(version, tuple)
    assert version >= cli_version.MIN_CLI_VERSIONS["claude"]


@pytest.mark.unit
def test_cli_status_survives_a_present_cli(monkeypatch: pytest.MonkeyPatch, staff_cli_installed) -> None:
    """The str-vs-tuple crash only appeared when ``which`` found the CLI."""
    staff_cli_installed("claude")
    monkeypatch.setattr(cli_version.shutil, "which", lambda exe, *a, **k: f"/usr/bin/{exe}")
    status = cli_version.cli_status("claude")
    assert status.installed and not status.outdated
