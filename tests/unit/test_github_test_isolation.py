"""Regression coverage for #1528: the suite never holds a real GitHub credential.

With ``GH_TOKEN`` (or GitHub App env, or a ``gh auth`` login) in the developer's
environment, code-request tests created real issues through ``gh_utils.gh_api_write``.
The autouse fixture in ``tests/conftest.py`` removes every credential source.
"""

from __future__ import annotations

import os
from pathlib import Path

import gh_client
import pytest

CREDENTIAL_ENV = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_FILE",
)


@pytest.mark.unit
def test_no_github_credential_env_is_visible() -> None:
    leaked = [name for name in CREDENTIAL_ENV if os.environ.get(name)]
    assert leaked == []


@pytest.mark.unit
def test_gh_client_has_no_token() -> None:
    with pytest.raises(gh_client.GhAuthError):
        gh_client._get_token()  # noqa: SLF001


@pytest.mark.unit
def test_gh_cli_config_is_an_empty_per_test_dir(tmp_path: Path) -> None:
    """The ``gh`` CLI sees no login: its config dir is this test's own, empty."""
    config = Path(os.environ["GH_CONFIG_DIR"])
    assert config.resolve().is_relative_to(tmp_path.resolve())
    assert not (config / "hosts.yml").exists()


@pytest.mark.unit
def test_a_test_can_still_opt_into_a_fake_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "fake-test-token")
    assert gh_client._get_token() == "fake-test-token"  # noqa: SLF001
