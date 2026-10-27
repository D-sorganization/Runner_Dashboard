"""All lease shapes protect runners at the actual poll-loop stop boundary (#3025)."""

from unittest.mock import Mock

import pytest
import runner_autoscaler as ra

UNITS = ["actions.runner.org.fixture-1.service", "actions.runner.org.fixture-2.service"]


class PollFinished(BaseException):
    """Stop a bounded test tick without the loop swallowing the signal."""


@pytest.mark.parametrize(
    ("leases", "expected"),
    [
        ({"virtual-env"}, []),
        ({"virtual-fingerprint"}, []),
        ({"github-claim-8"}, []),
        ({"fixture-1"}, [UNITS[1]]),
        ({UNITS[0]}, [UNITS[1]]),
        (set(), UNITS),
    ],
)
def test_scale_down_respects_virtual_leases(monkeypatch, caplog, leases, expected):
    sample = {"default": {"cpu": 100, "mem": 0, "load": 0, "disk": 0, "disk_free": 1000}, "nvme": {}, "hdd": {}}
    monkeypatch.setattr(ra, "_sample_all_pools", lambda: sample)
    monkeypatch.setattr(ra, "_list_runner_units", lambda: UNITS)
    monkeypatch.setattr(ra, "_classify_unit", lambda unit: "default")
    monkeypatch.setattr(ra, "_get_pool_config", lambda pool: {"min_online": 0})
    monkeypatch.setattr(ra, "_leased_runners", lambda: leases)
    monkeypatch.setattr(ra, "_unit_is_active", lambda unit: True)
    monkeypatch.setattr(ra, "_runner_is_busy", lambda unit: False)
    monkeypatch.setattr(ra, "_get_scheduled_pool_desired", lambda pool, count: count)
    monkeypatch.setattr(ra, "_is_stop_allowed", lambda pool: True)
    monkeypatch.setattr(ra, "_stop_in_cooldown", lambda pool: False)
    monkeypatch.setattr(ra, "_record_stop_action", lambda pool: None)
    monkeypatch.setattr(ra, "_notify_systemd", lambda state: None)
    monkeypatch.setattr(ra, "_send_watchdog", lambda: None)
    monkeypatch.setattr(ra, "process_disk_full_cordon_tick", lambda *args, **kwargs: False)
    monkeypatch.setattr(ra, "SUSTAIN_SECS", 0)
    monkeypatch.setattr(ra, "POLL_SECONDS", 1)
    monkeypatch.setattr(ra, "MAX_STEP", 2)
    monkeypatch.setattr(ra, "pool_last_overloaded", dict.fromkeys(["default", "nvme", "hdd"], 0.0))
    stops = Mock(return_value=True)
    monkeypatch.setattr(ra, "_stop_unit", stops)
    monkeypatch.setattr(ra, "_start_unit", Mock(side_effect=AssertionError("unexpected start")))
    ticks = [0]

    def sleep(seconds):
        ticks[0] += 1
        if ticks[0] == 3:
            raise PollFinished

    monkeypatch.setattr(ra.time, "sleep", sleep)
    with pytest.raises(PollFinished):
        ra._run_poll_loop()
    assert [call.args[0] for call in stops.call_args_list] == expected
    if any(lease.startswith(("virtual-", "github-claim-")) for lease in leases):
        assert "unresolved lease" in caplog.text
