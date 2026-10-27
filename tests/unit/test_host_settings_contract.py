"""Host settings contract (issue #3101)."""

from __future__ import annotations

import pytest
from staff.host_settings_contract import (
    HostSettingsDocument,
    resolve_role_leader,
)


@pytest.mark.unit
def test_missing_config_resolves_grokbot_first_for_every_role() -> None:
    for role in ("barb", "night-watch", "orchestrator"):
        resolved = resolve_role_leader(role, None)
        assert resolved.preference == "grokbot-first"
        assert resolved.leader == "grokbot"
        assert resolved.hosts_enabled["grokbot"] is True
        assert resolved.hosts_enabled["rd"] is True


@pytest.mark.unit
def test_per_role_override_beats_global_default() -> None:
    doc = HostSettingsDocument.from_dict(
        {
            "schema_version": 1,
            "global": {"preference": "grokbot-first"},
            "roles": {"barb": {"preference": "rd-first"}},
        }
    )
    assert resolve_role_leader("barb", doc).leader == "rd"
    assert resolve_role_leader("night-watch", doc).leader == "grokbot"


@pytest.mark.unit
def test_invalid_preference_fails_closed() -> None:
    with pytest.raises(ValueError, match="invalid preference"):
        HostSettingsDocument.from_dict({"global": {"preference": "hub-first"}})


@pytest.mark.unit
def test_unknown_top_level_key_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown top-level"):
        HostSettingsDocument.from_dict({"extra": 1})


@pytest.mark.unit
def test_rd_first_with_grokbot_disabled() -> None:
    doc = HostSettingsDocument.from_dict(
        {
            "global": {
                "preference": "rd-first",
                "hosts": {"grokbot": {"enabled": False}, "rd": {"enabled": True}},
            }
        }
    )
    resolved = resolve_role_leader("any-role", doc)
    assert resolved.leader == "rd"
    assert resolved.followers == ()


@pytest.mark.unit
def test_both_mode_lists_co_enabled_hosts() -> None:
    doc = HostSettingsDocument.from_dict({"global": {"preference": "both"}})
    resolved = resolve_role_leader("barb", doc)
    assert resolved.leader == "grokbot"
    assert resolved.followers == ("rd",)
