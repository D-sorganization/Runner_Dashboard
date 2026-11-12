"""Escalation trusted-actor policy."""

from __future__ import annotations

import pytest
from escalation.policy import is_trusted_github_actor, trusted_github_logins


@pytest.mark.unit
def test_trusted_logins_include_dieter_and_fleet_app() -> None:
    logins = trusted_github_logins()
    assert "dieterolson" in logins
    assert "d-sorggeneralagent[bot]" in logins


@pytest.mark.unit
def test_is_trusted_github_actor_case_insensitive() -> None:
    assert is_trusted_github_actor("DieterOlson")
    assert not is_trusted_github_actor("random-coder")
