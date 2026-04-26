"""
Tests for assistant_tools.py — Issue #89 tool-use layer.

Covers:
- Tool allowlist contents and Anthropic tool-definition shape
- Audit log record / history
- call_anthropic_with_tools (mocked httpx)
- execute_tool allowlist enforcement, confirmation contract, routing
"""

from __future__ import annotations  # noqa: E402

import os  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import httpx  # noqa: F401  # imported so we can patch httpx.AsyncClient  # noqa: E402

# Ensure the backend directory is on sys.path when running from repo root
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(BACKEND_DIR))

import assistant_tools  # noqa: E402  # noqa: E402
from assistant_tools import (  # noqa: E402  # noqa: E402
    _TOOL_AUDIT_LOG,
    TOOL_ALLOWLIST,
    _record_audit,
    get_audit_history,
    get_tool_definitions,
)


class TestToolAllowlist(unittest.TestCase):
    """TOOL_ALLOWLIST shape and Anthropic-compatibility."""

    def test_expected_tools_present(self) -> None:
        expected = {
            "list_open_prs",
            "list_open_issues",
            "get_failed_runs",
            "get_repos",
            "refresh_dashboard_data",
            "dispatch_agent_to_pr",
            "dispatch_agent_to_issue",
            "quick_dispatch_agent",
            "dispatch_remediation",
        }
        self.assertTrue(expected.issubset(set(TOOL_ALLOWLIST.keys())))

    def test_read_only_tools_do_not_require_confirmation(self) -> None:
        read_only = {
            "list_open_prs",
            "list_open_issues",
            "get_failed_runs",
            "get_repos",
            "refresh_dashboard_data",
        }
        for name in read_only:
            self.assertFalse(
                TOOL_ALLOWLIST[name]["requires_confirmation"],
                f"{name} should NOT require confirmation",
            )

    def test_state_changing_tools_require_confirmation(self) -> None:
        state_changing = {
            "dispatch_agent_to_pr",
            "dispatch_agent_to_issue",
            "quick_dispatch_agent",
            "dispatch_remediation",
        }
        for name in state_changing:
            self.assertTrue(
                TOOL_ALLOWLIST[name]["requires_confirmation"],
                f"{name} MUST require confirmation",
            )

    def test_tool_definitions_schema(self) -> None:
        defs = get_tool_definitions()
        self.assertEqual(len(defs), len(TOOL_ALLOWLIST))
        for defn in defs:
            self.assertIn("name", defn)
            self.assertIn("description", defn)
            self.assertIn("input_schema", defn)
            self.assertIsInstance(defn["input_schema"], dict)

    def test_all_tools_have_backend_endpoint(self) -> None:
        for name, spec in TOOL_ALLOWLIST.items():
            self.assertIn("backend_endpoint", spec, f"{name} missing backend_endpoint")


class TestAuditLog(unittest.TestCase):
    """_record_audit and get_audit_history."""

    def setUp(self) -> None:
        _TOOL_AUDIT_LOG.clear()

    def test_record_audit_appends_entry(self) -> None:
        entry = _record_audit(
            tool_name="list_open_prs",
            tool_call_id="call_001",
            inputs={},
            outcome="ok",
            success=True,
            approved_by="n/a",
            note="",
        )
        self.assertEqual(entry["tool_name"], "list_open_prs")
        self.assertTrue(entry["assistant"])
        self.assertIn("timestamp", entry)
        self.assertEqual(len(_TOOL_AUDIT_LOG), 1)

    def test_get_audit_history_newest_first(self) -> None:
        for i in range(5):
            _record_audit(
                tool_name=f"tool_{i}",
                tool_call_id=f"call_{i}",
                inputs={},
                outcome="ok",
                success=True,
                approved_by="n/a",
                note="",
            )
        history = get_audit_history(limit=5)
        # newest first → last appended should be first
        self.assertEqual(history[0]["tool_name"], "tool_4")

    def test_get_audit_history_limit_respected(self) -> None:
        for i in range(10):
            _record_audit(
                tool_name="list_open_prs",
                tool_call_id=f"c{i}",
                inputs={},
                outcome="ok",
                success=True,
                approved_by="n/a",
                note="",
            )
        self.assertEqual(len(get_audit_history(limit=3)), 3)

    def test_audit_entry_has_required_fields(self) -> None:
        entry = _record_audit(
            tool_name="dispatch_agent_to_pr",
            tool_call_id="call_xyz",
            inputs={"repository": "foo/bar", "number": 42},
            outcome="ok",
            success=True,
            approved_by="dieter",
            note="Approved via UI",
        )
        for field in (
            "timestamp",
            "tool_name",
            "tool_call_id",
            "inputs",
            "outcome",
            "success",
            "assistant",
            "approved_by",
            "note",
        ):
            self.assertIn(field, entry, f"Missing field: {field}")


class TestCallAnthropicWithTools(unittest.IsolatedAsyncioTestCase):
    """call_anthropic_with_tools — mocked httpx."""

    async def test_end_turn_response(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Here is your answer."}],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await assistant_tools.call_anthropic_with_tools(
                api_key="test-key",
                prompt="What is the state of the fleet?",
                context={"current_tab": "overview"},
            )

        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertIn("Here is your answer", result["message"]["content"])
        self.assertEqual(result["tool_calls"], [])

    async def test_tool_use_response(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": "I will fetch open PRs."},
                {
                    "type": "tool_use",
                    "id": "call_abc",
                    "name": "list_open_prs",
                    "input": {"limit": 10},
                },
            ],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await assistant_tools.call_anthropic_with_tools(
                api_key="test-key",
                prompt="Show me open PRs",
                context={"current_tab": "prs"},
            )

        self.assertEqual(result["stop_reason"], "tool_use")
        self.assertEqual(len(result["tool_calls"]), 1)
        tc = result["tool_calls"][0]
        self.assertEqual(tc["name"], "list_open_prs")
        self.assertEqual(tc["id"], "call_abc")
        self.assertFalse(tc["requires_confirmation"])

    async def test_state_changing_tool_marked_requires_confirmation(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_dispatch",
                    "name": "dispatch_agent_to_pr",
                    "input": {
                        "repository": "D-sorganization/foo",
                        "number": 5,
                        "provider": "claude_code_cli",
                        "prompt": "Fix it",
                    },
                }
            ],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await assistant_tools.call_anthropic_with_tools(
                api_key="test-key",
                prompt="Fix PR 5 in D-sorganization/foo",
                context={"current_tab": "prs"},
            )

        tc = result["tool_calls"][0]
        self.assertTrue(tc["requires_confirmation"])

    async def test_raises_on_api_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with self.assertRaises(RuntimeError) as ctx:
                await assistant_tools.call_anthropic_with_tools(
                    api_key="bad-key",
                    prompt="anything",
                    context={"current_tab": "overview"},
                )
        self.assertIn("401", str(ctx.exception))


class TestExecuteTool(unittest.IsolatedAsyncioTestCase):
    """execute_tool routing and confirmation enforcement."""

    def setUp(self) -> None:
        _TOOL_AUDIT_LOG.clear()

    async def test_unknown_tool_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            await assistant_tools.execute_tool(
                tool_name="rm_rf_everything",
                tool_call_id="c1",
                inputs={},
                confirmation=None,
                gh_api_fn=AsyncMock(),
                dispatch_fn=AsyncMock(),
            )

    async def test_state_changing_without_confirmation_raises(self) -> None:
        with self.assertRaises(PermissionError):
            await assistant_tools.execute_tool(
                tool_name="dispatch_agent_to_pr",
                tool_call_id="c2",
                inputs={
                    "repository": "foo/bar",
                    "number": 1,
                    "provider": "p",
                    "prompt": "x",
                },
                confirmation=None,
                gh_api_fn=AsyncMock(),
                dispatch_fn=AsyncMock(),
            )

    async def test_readonly_tool_auto_executes(self) -> None:
        gh_api_fn = AsyncMock(return_value={"prs": []})

        result = await assistant_tools.execute_tool(
            tool_name="list_open_prs",
            tool_call_id="c3",
            inputs={},
            confirmation=None,
            gh_api_fn=gh_api_fn,
            dispatch_fn=AsyncMock(),
        )

        self.assertTrue(result["result"] is not None)
        self.assertEqual(len(_TOOL_AUDIT_LOG), 1)
        entry = _TOOL_AUDIT_LOG[0]
        self.assertTrue(entry["success"])
        self.assertEqual(entry["approved_by"], "n/a")

    async def test_state_changing_with_confirmation_succeeds(self) -> None:
        dispatch_fn = AsyncMock(return_value={"status": "dispatched"})

        result = await assistant_tools.execute_tool(
            tool_name="dispatch_agent_to_pr",
            tool_call_id="c4",
            inputs={
                "repository": "foo/bar",
                "number": 3,
                "provider": "p",
                "prompt": "x",
            },
            confirmation={"approved_by": "dieter", "note": "confirmed in UI"},
            gh_api_fn=AsyncMock(),
            dispatch_fn=dispatch_fn,
        )

        self.assertTrue(result["result"] is not None)
        self.assertEqual(len(_TOOL_AUDIT_LOG), 1)
        entry = _TOOL_AUDIT_LOG[0]
        self.assertEqual(entry["approved_by"], "dieter")

    async def test_failed_tool_still_recorded_in_audit(self) -> None:
        gh_api_fn = AsyncMock(side_effect=RuntimeError("network error"))

        await assistant_tools.execute_tool(
            tool_name="list_open_prs",
            tool_call_id="c5",
            inputs={},
            confirmation=None,
            gh_api_fn=gh_api_fn,
            dispatch_fn=AsyncMock(),
        )

        self.assertEqual(len(_TOOL_AUDIT_LOG), 1)
        entry = _TOOL_AUDIT_LOG[0]
        self.assertFalse(entry["success"])
        self.assertIn("network error", entry["outcome"])


if __name__ == "__main__":
    unittest.main()
