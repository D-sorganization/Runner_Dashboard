from __future__ import annotations

import importlib.util
import io
import json
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "clients/fleet/codex/barb_wrapper.py"
SPEC = importlib.util.spec_from_file_location("codex_barb_wrapper", SCRIPT)
assert SPEC and SPEC.loader
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


def test_installer_renders_prompt_to_requested_codex_home(tmp_path: Path) -> None:
    (tmp_path / "hooks.json").write_text('{"hooks":{"Stop":[]}}', encoding="utf-8")
    prompt = wrapper.install(tmp_path)

    assert prompt == tmp_path / "prompts/barb.md"
    content = prompt.read_text(encoding="utf-8")
    assert "@barb" in content
    assert "/barb chat" in content
    assert "rd-threads" in content
    assert "FLEET_API_TOKEN" not in content
    assert "https://" not in content
    config = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))
    assert config["hooks"]["Stop"] == []
    assert config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 2


def test_message_and_chat_call_only_shared_entrypoint_and_return_correlation_id() -> None:
    invoke = Mock(return_value=json.dumps({"ok": True, "correlation_id": "corr-123"}))

    assert wrapper.dispatch(["hello Barb"], invoke=invoke) == "corr-123"
    assert wrapper.dispatch(["chat", "continue this"], invoke=invoke) == "corr-123"
    assert invoke.call_args_list[0].args == (
        "send",
        {"to": "barb", "kind": "update", "summary": "hello Barb", "body": "hello Barb"},
    )
    assert invoke.call_args_list[1].args == (
        "send",
        {"to": "barb", "kind": "update", "summary": "Codex Barb chat", "body": "continue this"},
    )
    assert all("token" not in json.dumps(call.args).lower() for call in invoke.call_args_list)


def test_wrapper_returns_without_waiting_for_a_reply() -> None:
    invoke = Mock(return_value='{"ok": true, "correlation_id": "corr-fast"}')

    assert wrapper.dispatch(["hello"], invoke=invoke) == "corr-fast"
    invoke.assert_called_once()


def test_invoke_only_calls_shared_cli_without_credentials_or_hub_url() -> None:
    with patch.object(
        wrapper.subprocess,
        "run",
        return_value=Mock(returncode=0, stdout='{"ok":true}', stderr=""),
    ) as run:
        wrapper._invoke("inbox", {})

    command = run.call_args.args[0]
    assert Path(command[1]) == ROOT / "scripts/barb_cli.py"
    assert "token" not in json.dumps(command).lower()
    assert "https://" not in json.dumps(command)
    assert run.call_args.kwargs["timeout"] == 2.0


def test_mention_hook_reads_inbox_only_for_explicit_barb_mentions(tmp_path: Path, capsys) -> None:
    hook_path = tmp_path / "hooks/barb_inbox.py"
    hook_path.parent.mkdir()
    shutil.copyfile(ROOT / "clients/fleet/codex/barb_inbox.py", hook_path)
    (hook_path.parent / "rd_threads_cli").write_text(str(ROOT / "scripts/barb_cli.py"), encoding="utf-8")
    hook_spec = importlib.util.spec_from_file_location("codex_barb_inbox", hook_path)
    assert hook_spec and hook_spec.loader
    hook = importlib.util.module_from_spec(hook_spec)
    hook_spec.loader.exec_module(hook)

    with patch.object(hook.sys, "stdin", io.StringIO('{"prompt":"hello"}')):
        with patch.object(hook.subprocess, "run") as run:
            assert hook.main() == 0
            run.assert_not_called()
    assert capsys.readouterr().out == ""

    inbox = '{"ok":true,"items":[{"summary":"ready"}]}'
    with patch.object(hook.sys, "stdin", io.StringIO('{"prompt":"@barb status"}')):
        with patch.object(hook.subprocess, "run", return_value=Mock(returncode=0, stdout=inbox)) as run:
            assert hook.main() == 0
            assert Path(run.call_args.args[0][1]) == ROOT / "scripts/barb_cli.py"
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "ready" in output["hookSpecificOutput"]["additionalContext"]
