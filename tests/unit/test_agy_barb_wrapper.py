"""Antigravity (agy) /barb wrapper (#3069): workflow template renders per provider, the
hook calls only the shared rd-threads entrypoint (mocked), and no token/secret ever
reaches its output."""

from unittest.mock import MagicMock, patch

from scripts.wrappers import agy_barb_hook as hook
from scripts.wrappers import agy_barb_template as template
from scripts.wrappers import sync_agy_barb_workflow as sync


def test_template_renders_the_shared_body_with_the_provider_name() -> None:
    rendered = template.render("agy")
    assert "scripts/barb_cli.py" in rendered and "/barb chat" in rendered


def test_sync_writes_the_rendered_template_to_every_registered_provider_path(tmp_path, monkeypatch) -> None:
    fake_path = tmp_path / "workflows" / "barb.md"
    monkeypatch.setattr(sync, "PATHS", {"agy": fake_path})
    written = sync.sync()
    assert written == [fake_path]
    assert "scripts/barb_cli.py" in fake_path.read_text(encoding="utf-8")


def test_sync_targets_the_agents_workflow_path_agy_reads() -> None:
    # agy reads .agents/ (RM #2567); the legacy .agent/ tree is gone (#3664).
    root = sync._ROOT
    assert sync.PATHS == {"agy": root / ".agents" / "workflows" / "barb.md"}
    assert sync.PATHS["agy"].is_file()
    assert not (root / ".agent").exists()


def test_fetch_unread_calls_the_shared_entrypoint_with_the_inbox_verb_and_no_args() -> None:
    cli = MagicMock()
    with patch.object(hook.barb_cli, "dispatch") as dispatch:
        dispatch.return_value = {"ok": True, "items": []}
        hook.fetch_unread(cli)
    dispatch.assert_called_once_with(cli, "inbox", {})


def test_render_formats_unread_items_mentioning_this_agent() -> None:
    result = {"ok": True, "items": [{"kind": "update", "from": "codex", "summary": "merged #1"}]}
    text = hook.render(result)
    assert text.startswith("## Unread rd-threads messages for @agy")
    assert "codex" in text and "merged #1" in text


def test_render_is_empty_when_there_is_nothing_unread_or_the_call_failed() -> None:
    assert hook.render({"ok": True, "items": []}) == ""
    assert hook.render({"ok": False, "error": "missing_scope"}) == ""


def test_hook_main_never_prints_the_token_even_if_the_hub_echoes_it(monkeypatch, capsys) -> None:
    secret = "svc_TOPSECRET_never_print_me"  # pragma: allowlist secret
    monkeypatch.setenv("FLEET_API_TOKEN", secret)
    fake_cli = MagicMock()
    monkeypatch.setattr(hook.barb_cli, "build_cli", lambda: fake_cli)
    monkeypatch.setattr(hook.barb_cli, "dispatch", lambda cli, verb, args: {"ok": True, "items": []})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
    assert hook.main() == 0
    assert secret not in capsys.readouterr().out
