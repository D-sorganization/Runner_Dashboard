from staff.role_provider_chains import approved_chain_for_role
from staff.run_token_scopes import extra_run_scopes_for_role


def test_board_secretary_has_chat_scope():
    assert "staff.chat" in extra_run_scopes_for_role("board-secretary")
    assert "staff.chat" not in extra_run_scopes_for_role("maintenance")


def test_fleet_watch_prefers_codex():
    assert approved_chain_for_role("fleet-watch")[0] == "codex"


def test_blocked_chair_result_is_failed_even_when_cli_exits_zero(tmp_path):
    from types import SimpleNamespace

    from staff.execution_result import classify_execution_result

    result = (
        "STAFF_RESULT: Blocked: prepared local Board packet; 0/4 seats convened "
        "because staff.chat was denied. No consensus or PR."
    )
    status, failure, _, _, _ = classify_execution_result(
        cancelled=False,
        rc=0,
        result_line=result,
        provider="codex",
        transcript_path=None,
        run=SimpleNamespace(role="board-secretary"),
    )
    assert (status, failure) == ("failed", "board_chair_failed")
