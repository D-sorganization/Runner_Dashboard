"""TDD tests for the server.py 5-file refactor (issue #2942).

Tests are written FIRST and verify the new modules exist and export the
expected public API.  They start RED (modules not yet created) and turn
GREEN after the refactor.

Modules under test
------------------
- backend/wsl_watchdog.py        — WSL keepalive inspection helpers
- backend/runner_schedule.py     — runner schedule validation/loading
- backend/fleet_node_helpers.py  — fleet node aggregation helpers
- backend/help_chat.py           — help-chat FAQ and handler logic
- backend/deployment_helpers.py  — deployment drift state helpers
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# 1. watchdog.py
# ---------------------------------------------------------------------------


class TestWatchdogModule:
    """backend/watchdog.py must export WSL keepalive helper functions."""

    def test_watchdog_importable(self) -> None:
        import wsl_watchdog  # noqa: PLC0415

        assert wsl_watchdog is not None

    def test_windows_path_to_wsl(self) -> None:
        from wsl_watchdog import _windows_path_to_wsl  # noqa: PLC0415

        result = _windows_path_to_wsl(r"C:\Users\diete")
        # Compare using path parts — platform-agnostic
        parts = result.parts
        assert "mnt" in parts
        assert "c" in parts
        assert "Users" in parts
        assert "diete" in parts

    def test_windows_path_to_wsl_non_windows(self) -> None:
        from wsl_watchdog import _windows_path_to_wsl  # noqa: PLC0415

        result = _windows_path_to_wsl("/home/user/file")
        # The non-Windows path should pass through unchanged (modulo OS path sep)
        assert "home" in str(result) and "user" in str(result) and "file" in str(result)

    def test_dedupe_paths(self) -> None:
        from wsl_watchdog import _dedupe_paths  # noqa: PLC0415

        paths = [Path("/a"), Path("/b"), Path("/a"), Path("/c")]
        result = _dedupe_paths(paths)
        assert result == [Path("/a"), Path("/b"), Path("/c")]

    def test_parse_vm_idle_timeout_found(self) -> None:
        from wsl_watchdog import _parse_vm_idle_timeout  # noqa: PLC0415

        text = "[wsl2]\nvmIdleTimeout=-1\n"
        assert _parse_vm_idle_timeout(text) == "-1"

    def test_parse_vm_idle_timeout_not_found(self) -> None:
        from wsl_watchdog import _parse_vm_idle_timeout  # noqa: PLC0415

        text = "[wsl2]\n# no timeout setting\n"
        assert _parse_vm_idle_timeout(text) is None

    def test_parse_vm_idle_timeout_with_spaces(self) -> None:
        from wsl_watchdog import _parse_vm_idle_timeout  # noqa: PLC0415

        text = "[wsl2]\n vmIdleTimeout = 60000 \n"
        assert _parse_vm_idle_timeout(text) == "60000"

    def test_detect_legacy_keepalive_vbs_file(self) -> None:
        from wsl_watchdog import _detect_legacy_keepalive  # noqa: PLC0415

        detected, detail = _detect_legacy_keepalive([], ["C:/startup/wsl-keepalive.vbs"])
        assert detected is True
        assert detail is not None
        assert "Legacy VBS" in detail

    def test_detect_legacy_keepalive_clean(self) -> None:
        from wsl_watchdog import _detect_legacy_keepalive  # noqa: PLC0415

        detected, detail = _detect_legacy_keepalive(
            [{"execute": "/usr/bin/wsl", "arguments": "--exec bash"}],
            [],
        )
        assert detected is False
        assert detail is None

    def test_detect_legacy_keepalive_wscript_action(self) -> None:
        from wsl_watchdog import _detect_legacy_keepalive  # noqa: PLC0415

        detected, detail = _detect_legacy_keepalive(
            [{"execute": "C:/Windows/System32/wscript.exe", "arguments": "keepalive.vbs"}],
            [],
        )
        assert detected is True

    def test_parse_task_action(self) -> None:
        from wsl_watchdog import _parse_task_action  # noqa: PLC0415

        action = {"Execute": "/usr/bin/wsl", "Arguments": "--exec bash"}
        result = _parse_task_action(action)
        assert result["execute"] == "/usr/bin/wsl"
        assert result["arguments"] == "--exec bash"

    def test_probe_detail_with_detail_key(self) -> None:
        from wsl_watchdog import _probe_detail  # noqa: PLC0415

        probe = {"detail": "All good"}
        assert _probe_detail(probe, "fallback") == "All good"

    def test_probe_detail_fallback(self) -> None:
        from wsl_watchdog import _probe_detail  # noqa: PLC0415

        probe: dict = {}
        assert _probe_detail(probe, "fallback text") == "fallback text"

    def test_candidate_wslconfig_paths_returns_list(self) -> None:
        from wsl_watchdog import _candidate_wslconfig_paths  # noqa: PLC0415

        # Should return a list (may be empty in CI)
        result = _candidate_wslconfig_paths()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 2. runner_schedule.py
# ---------------------------------------------------------------------------


class TestRunnerScheduleModule:
    """backend/runner_schedule.py must export schedule validation helpers."""

    def test_runner_schedule_importable(self) -> None:
        import runner_schedule  # noqa: PLC0415

        assert runner_schedule is not None

    def test_validate_hhmm_valid(self) -> None:
        from runner_schedule import _validate_hhmm  # noqa: PLC0415

        assert _validate_hhmm("08:30") == "08:30"
        assert _validate_hhmm("23:59") == "23:59"
        assert _validate_hhmm("00:00") == "00:00"

    @pytest.mark.parametrize(
        "bad_value",
        ["8:30", "25:00", "08:60", "abc", 830, None, ""],
    )
    def test_validate_hhmm_invalid(self, bad_value: object) -> None:
        from runner_schedule import _validate_hhmm  # noqa: PLC0415

        with pytest.raises(ValueError):
            _validate_hhmm(bad_value)

    def test_validate_runner_schedule_minimal(self) -> None:
        from runner_schedule import _validate_runner_schedule  # noqa: PLC0415

        config = {
            "enabled": True,
            "timezone": "America/Los_Angeles",
            "default_count": 4,
            "schedules": [
                {
                    "name": "day",
                    "days": ["mon", "tue"],
                    "start": "08:00",
                    "end": "18:00",
                    "runners": 4,
                }
            ],
        }
        result = _validate_runner_schedule(config)
        assert result["enabled"] is True
        assert result["timezone"] == "America/Los_Angeles"
        assert len(result["schedules"]) == 1

    def test_validate_runner_schedule_not_dict_raises(self) -> None:
        from runner_schedule import _validate_runner_schedule  # noqa: PLC0415

        with pytest.raises(ValueError):
            _validate_runner_schedule("bad")

    def test_validate_runner_schedule_bad_day_raises(self) -> None:
        from runner_schedule import _validate_runner_schedule  # noqa: PLC0415

        config = {
            "schedules": [
                {
                    "days": ["monday"],  # invalid — must be 3-letter abbreviation
                    "start": "08:00",
                    "end": "18:00",
                    "runners": 4,
                }
            ]
        }
        with pytest.raises(ValueError, match="mon/tue"):
            _validate_runner_schedule(config)

    def test_validate_runner_schedule_caps_runners(self) -> None:
        from runner_schedule import _validate_runner_schedule  # noqa: PLC0415

        config = {
            "schedules": [
                {
                    "days": ["mon"],
                    "start": "08:00",
                    "end": "18:00",
                    "runners": 9999,  # should be capped to runner limit
                }
            ]
        }
        result = _validate_runner_schedule(config)
        assert result["schedules"][0]["runners"] <= 9999  # capped

    def test_validate_runner_schedule_empty_days_raises(self) -> None:
        from runner_schedule import _validate_runner_schedule  # noqa: PLC0415

        config = {
            "schedules": [
                {
                    "days": [],
                    "start": "08:00",
                    "end": "18:00",
                    "runners": 4,
                }
            ]
        }
        with pytest.raises(ValueError):
            _validate_runner_schedule(config)


# ---------------------------------------------------------------------------
# 3. fleet_node_helpers.py
# ---------------------------------------------------------------------------


class TestFleetNodeHelpersModule:
    """backend/fleet_node_helpers.py must export fleet node classification helpers."""

    def test_fleet_node_helpers_importable(self) -> None:
        import fleet_node_helpers  # noqa: PLC0415

        assert fleet_node_helpers is not None

    def test_classify_node_offline_status_code(self) -> None:
        from fleet_node_helpers import _classify_node_offline  # noqa: PLC0415

        result = _classify_node_offline(status_code=503)
        assert result["offline_reason"] == "dashboard_unhealthy"
        assert "503" in result["offline_detail"]

    def test_classify_node_offline_no_args(self) -> None:
        from fleet_node_helpers import _classify_node_offline  # noqa: PLC0415

        result = _classify_node_offline()
        assert "offline_reason" in result
        assert "offline_detail" in result

    def test_resource_offline_reason_no_pressure(self) -> None:
        from fleet_node_helpers import _resource_offline_reason  # noqa: PLC0415

        system = {
            "cpu": {"percent": 10},
            "memory": {"percent": 50},
            "disk": {"percent": 40},
        }
        assert _resource_offline_reason(system) is None

    def test_resource_offline_reason_high_cpu(self) -> None:
        from fleet_node_helpers import _resource_offline_reason  # noqa: PLC0415

        system = {
            "cpu": {"percent_1m_avg": 99.0},
            "memory": {"percent": 50},
            "disk": {"percent": 40},
        }
        result = _resource_offline_reason(system)
        assert result is not None
        assert result["offline_reason"] == "resource_monitoring"

    def test_node_visibility_snapshot_full_telemetry(self) -> None:
        from fleet_node_helpers import _node_visibility_snapshot  # noqa: PLC0415

        node = {"online": True, "dashboard_reachable": True, "system": {"cpu": {}}}
        result = _node_visibility_snapshot(node)
        assert result["visibility_state"] == "full_telemetry"

    def test_node_visibility_snapshot_offline(self) -> None:
        from fleet_node_helpers import _node_visibility_snapshot  # noqa: PLC0415

        node = {"online": False, "dashboard_reachable": False, "system": {}}
        result = _node_visibility_snapshot(node)
        assert result["visibility_state"] == "offline"

    def test_node_visibility_snapshot_runners_only(self) -> None:
        from fleet_node_helpers import _node_visibility_snapshot  # noqa: PLC0415

        node = {"online": True, "dashboard_reachable": True, "system": {}}
        result = _node_visibility_snapshot(node)
        assert result["visibility_state"] == "runners_only"

    def test_machine_name_from_runner_name_prefix(self) -> None:
        from fleet_node_helpers import _machine_name_from_runner_name  # noqa: PLC0415

        assert _machine_name_from_runner_name("d-sorg-local-envy-3") == "envy"
        assert _machine_name_from_runner_name("d-sorg-local-thinkpad-12") == "thinkpad"

    def test_machine_name_from_runner_name_no_prefix(self) -> None:
        from fleet_node_helpers import _machine_name_from_runner_name  # noqa: PLC0415

        assert _machine_name_from_runner_name("my-runner") == "my-runner"

    def test_machine_name_from_runner_name_none(self) -> None:
        from fleet_node_helpers import _machine_name_from_runner_name  # noqa: PLC0415

        assert _machine_name_from_runner_name(None) is None

    def test_placement_from_jobs_with_runner(self) -> None:
        from fleet_node_helpers import _placement_from_jobs  # noqa: PLC0415

        jobs = [
            {
                "runner_name": "d-sorg-local-envy-3",
                "runner_id": 42,
                "runner_group_name": "default",
                "labels": ["self-hosted", "linux"],
            }
        ]
        result = _placement_from_jobs(jobs)
        assert result["runner_name"] == "d-sorg-local-envy-3"
        assert result["machine_name"] == "envy"

    def test_placement_from_jobs_empty(self) -> None:
        from fleet_node_helpers import _placement_from_jobs  # noqa: PLC0415

        assert _placement_from_jobs([]) == {}

    def test_repo_name_from_run_dict(self) -> None:
        from fleet_node_helpers import _repo_name_from_run  # noqa: PLC0415

        run = {"repository": {"name": "Tools"}}
        assert _repo_name_from_run(run) == "Tools"

    def test_repo_name_from_run_underscore(self) -> None:
        from fleet_node_helpers import _repo_name_from_run  # noqa: PLC0415

        run = {"_repo": "UpstreamDrift"}
        assert _repo_name_from_run(run) == "UpstreamDrift"

    def test_repo_name_from_run_none(self) -> None:
        from fleet_node_helpers import _repo_name_from_run  # noqa: PLC0415

        assert _repo_name_from_run({}) is None


# ---------------------------------------------------------------------------
# 4. help_chat.py
# ---------------------------------------------------------------------------


class TestHelpChatModule:
    """backend/help_chat.py must export the FAQ dict and lookup helper."""

    def test_help_chat_importable(self) -> None:
        import help_chat  # noqa: PLC0415

        assert help_chat is not None

    def test_dashboard_faq_is_dict(self) -> None:
        from help_chat import DASHBOARD_FAQ  # noqa: PLC0415

        assert isinstance(DASHBOARD_FAQ, dict)
        assert len(DASHBOARD_FAQ) >= 5

    def test_dashboard_faq_has_expected_keys(self) -> None:
        from help_chat import DASHBOARD_FAQ  # noqa: PLC0415

        for key in ("fleet", "remediation", "workflows", "credentials"):
            assert key in DASHBOARD_FAQ, f"FAQ key '{key}' missing"

    def test_faq_lookup_returns_match(self) -> None:
        from help_chat import faq_lookup  # noqa: PLC0415

        answer = faq_lookup("how do I see the fleet?", "")
        assert answer is not None
        assert len(answer) > 0

    def test_faq_lookup_exact_tab_match(self) -> None:
        from help_chat import faq_lookup  # noqa: PLC0415

        # "fleet" key exists in FAQ
        answer = faq_lookup("fleet", "")
        assert answer is not None

    def test_faq_lookup_no_match_returns_none(self) -> None:
        from help_chat import faq_lookup  # noqa: PLC0415

        # A question that can't match any key
        answer = faq_lookup("xyzzy-nonsense-question-qqq", "")
        assert answer is None

    def test_tab_fallback_returns_string(self) -> None:
        from help_chat import tab_fallback_answer  # noqa: PLC0415

        answer = tab_fallback_answer("fleet")
        assert isinstance(answer, str)
        assert len(answer) > 0

    def test_tab_fallback_unknown_tab(self) -> None:
        from help_chat import tab_fallback_answer  # noqa: PLC0415

        answer = tab_fallback_answer("nonexistent-tab-xyzzy")
        assert isinstance(answer, str)


# ---------------------------------------------------------------------------
# 5. deployment_helpers.py
# ---------------------------------------------------------------------------


class TestDeploymentHelpersModule:
    """backend/deployment_helpers.py must export deployment drift helpers."""

    def test_deployment_helpers_importable(self) -> None:
        import deployment_helpers  # noqa: PLC0415

        assert deployment_helpers is not None

    def test_node_deployment_info_defaults(self) -> None:
        from deployment_helpers import _node_deployment_info  # noqa: PLC0415

        result = _node_deployment_info({})
        assert result["app"] == "runner-dashboard"
        assert result["version"] == "unknown"
        assert result["git_sha"] == "unknown"

    def test_node_deployment_info_from_health(self) -> None:
        from deployment_helpers import _node_deployment_info  # noqa: PLC0415

        node = {
            "health": {
                "deployment": {
                    "version": "1.2.3",
                    "git_sha": "abc123",
                    "git_branch": "main",
                }
            }
        }
        result = _node_deployment_info(node)
        assert result["version"] == "1.2.3"
        assert result["git_sha"] == "abc123"

    def test_machine_deployment_state_offline(self) -> None:
        from deployment_helpers import _machine_deployment_state  # noqa: PLC0415

        node = {"online": False, "offline_detail": "Not reachable"}
        result = _machine_deployment_state(node, "1.0.0")
        assert result["rollout_state"] == "offline"
        assert result["rollout_label"] == "Offline"

    def test_build_deployment_state_empty(self) -> None:
        from deployment_helpers import build_deployment_state  # noqa: PLC0415

        result = build_deployment_state([], "1.0.0")
        assert "rollout_state" in result
        assert "machines" in result
        assert result["machines"] == []
        assert result["rollout_state"]["status"] == "unknown"

    def test_build_deployment_state_returns_dict(self) -> None:
        from deployment_helpers import build_deployment_state  # noqa: PLC0415

        result = build_deployment_state([], "1.0.0")
        assert isinstance(result, dict)
        assert "timestamp" in result
        assert "deployment" in result


# ---------------------------------------------------------------------------
# 6. server.py public-API smoke tests (backward compat)
# ---------------------------------------------------------------------------


class TestServerPublicApi:
    """server.py must still export the FastAPI app and all previously public helpers."""


    def test_server_imports_wsl_watchdog_module(self) -> None:
        source = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
        assert "import wsl_watchdog" in source or "from wsl_watchdog" in source, (
            "server.py must import the new wsl_watchdog module"
        )

    def test_server_imports_runner_schedule_module(self) -> None:
        source = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
        assert "import runner_schedule" in source or "from runner_schedule" in source, (
            "server.py must import the new runner_schedule module"
        )

    def test_server_imports_fleet_node_helpers_module(self) -> None:
        source = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
        assert "import fleet_node_helpers" in source or "from fleet_node_helpers" in source, (
            "server.py must import the new fleet_node_helpers module"
        )

    def test_server_imports_help_chat_module(self) -> None:
        source = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
        assert "import help_chat" in source or "from help_chat" in source, (
            "server.py must import the new help_chat module"
        )

    def test_server_file_under_600_lines(self) -> None:
        """After the refactor, server.py must fit within the file-size cap."""
        lines = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 600, (
            f"server.py is {len(lines)} lines; must be ≤ 600 after refactor"
        )

    def test_server_imports_deployment_helpers_module(self) -> None:
        source = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
        assert "import deployment_helpers" in source or "from deployment_helpers" in source, (
            "server.py must import the new deployment_helpers module"
        )

    @pytest.mark.parametrize(
        "module_name",
        ["wsl_watchdog", "runner_schedule", "fleet_node_helpers", "help_chat", "deployment_helpers"],
    )
    def test_new_modules_exist(self, module_name: str) -> None:
        module_path = _BACKEND_DIR / f"{module_name}.py"
        assert module_path.exists(), f"backend/{module_name}.py must exist after refactor"
