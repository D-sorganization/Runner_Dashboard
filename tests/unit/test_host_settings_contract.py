"""Host settings contract (#3101)."""

from __future__ import annotations

import pytest
from staff.host_settings_contract import HostSettingsDocument, resolve_role_leader


@pytest.mark.unit
@pytest.mark.parametrize("role", ("barb", "night-watch"))
def test_missing_config_is_grokbot_first(role: str) -> None:
    r = resolve_role_leader(role, None)
    assert r.preference == "grokbot-first" and r.leader == "grokbot"


@pytest.mark.unit
def test_role_override_beats_global() -> None:
    doc = HostSettingsDocument.from_dict(
        {"global": {"preference": "grokbot-first"}, "roles": {"barb": {"preference": "rd-first"}}}
    )
    assert resolve_role_leader("barb", doc).leader == "rd"
    assert resolve_role_leader("other", doc).leader == "grokbot"


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    ({"global": {"preference": "hub-first"}}, {"extra": 1}),
)
def test_invalid_config_fails_closed(payload: dict) -> None:
    with pytest.raises(ValueError):
        HostSettingsDocument.from_dict(payload)
