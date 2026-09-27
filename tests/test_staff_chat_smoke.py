"""Tests for scripts/staff_chat_smoke.py (#1638)."""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "staff_chat_smoke.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("staff_chat_smoke", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves its module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_check_thread_all_pass() -> None:
    smoke = _load()
    messages: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "author": "user", "kind": "text", "body_md": "Status?"},
        {
            "seq": 2,
            "author_kind": "system",
            "author": "system",
            "kind": "ack",
            "meta": {"acknowledgement": True},
        },
        {
            "seq": 3,
            "author_kind": "role",
            "author": "barb",
            "kind": "text",
            "body_md": "Fleet is nominal.",
            "delivery": "complete",
        },
    ]
    checks = smoke.check_thread(messages)
    assert len(checks) == 3
    assert [c.name for c in checks] == ["reply complete", "reply non-empty", "ack before reply"]
    assert all(c.ok for c in checks)
    assert "user=1, ack=2, reply=3" in checks[2].detail


def test_check_thread_ack_after_reply_fails() -> None:
    smoke = _load()
    # Regression #1630: acknowledgement seq arrives after role reply seq
    messages: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "author": "user", "kind": "text", "body_md": "Status?"},
        {
            "seq": 2,
            "author_kind": "role",
            "author": "barb",
            "kind": "text",
            "body_md": "Premature reply.",
            "delivery": "complete",
        },
        {
            "seq": 3,
            "author_kind": "system",
            "author": "system",
            "kind": "ack",
            "meta": {"acknowledgement": True},
        },
    ]
    checks = smoke.check_thread(messages)
    assert checks[0].ok is True
    assert checks[1].ok is True
    assert checks[2].name == "ack before reply"
    assert checks[2].ok is False
    assert "user=1" in checks[2].detail
    assert "ack=3" in checks[2].detail
    assert "reply=2" in checks[2].detail


def test_check_thread_empty_body_and_missing_role() -> None:
    smoke = _load()
    # Empty reply body
    messages_empty: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "body_md": "Hi"},
        {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
        {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "   "},
    ]
    checks = smoke.check_thread(messages_empty)
    assert checks[0].ok is True
    assert checks[1].name == "reply non-empty"
    assert checks[1].ok is False

    # No role message
    messages_no_role: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "body_md": "Hi"},
        {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
    ]
    checks_no_role = smoke.check_thread(messages_no_role)
    assert checks_no_role[0].name == "reply complete"
    assert checks_no_role[0].ok is False
    assert checks_no_role[1].ok is False
    assert checks_no_role[2].ok is False


def test_run_smoke_polls_while_pending_then_succeeds() -> None:
    smoke = _load()
    sleep_calls: list[float] = []
    current_time = 50.0

    def fake_sleep(secs: float) -> None:
        nonlocal current_time
        sleep_calls.append(secs)
        current_time += secs

    def fake_clock() -> float:
        return current_time

    poll_count = 0

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        nonlocal poll_count
        if method == "POST" and url.endswith("/api/v1/staff/threads"):
            return 201, {"id": "th-smoke-1"}
        if method == "POST" and "/messages" in url:
            return 202, {}
        if method == "GET":
            poll_count += 1
            if poll_count == 1:
                return 200, {
                    "thread": {"id": "th-smoke-1"},
                    "messages": [
                        {"seq": 1, "author_kind": "user", "body_md": "Q"},
                        {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                        {"seq": 3, "author_kind": "role", "delivery": "pending", "body_md": ""},
                    ],
                }
            return 200, {
                "thread": {"id": "th-smoke-1"},
                "messages": [
                    {"seq": 1, "author_kind": "user", "body_md": "Q"},
                    {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                    {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "All good."},
                ],
            }
        return 404, {}

    checks, elapsed = smoke.run_smoke(
        base_url="https://dashboard.example.com",
        role="barb",
        question="What is active?",
        timeout_s=30.0,
        transport=fake_transport,
        sleep=fake_sleep,
        clock=fake_clock,
        poll_s=2.5,
    )
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.5
    assert poll_count == 2
    assert all(c.ok for c in checks)
    assert elapsed == 2.5


def test_run_smoke_timeout_reply_stays_pending() -> None:
    smoke = _load()
    current_time = 0.0

    def fake_sleep(secs: float) -> None:
        nonlocal current_time
        current_time += secs

    def fake_clock() -> float:
        return current_time

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and url.endswith("/api/v1/staff/threads"):
            return 201, {"id": "th-timeout"}
        if method == "POST" and "/messages" in url:
            return 202, {}
        if method == "GET":
            return 200, {
                "thread": {"id": "th-timeout"},
                "messages": [
                    {"seq": 1, "author_kind": "user", "body_md": "Q"},
                    {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                    {"seq": 3, "author_kind": "role", "delivery": "pending", "body_md": ""},
                ],
            }
        return 404, {}

    checks, elapsed = smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        role="barb",
        question="What is active?",
        timeout_s=10.0,
        transport=fake_transport,
        sleep=fake_sleep,
        clock=fake_clock,
        poll_s=5.0,
    )
    assert elapsed >= 10.0
    reply_complete = next(c for c in checks if c.name == "reply complete")
    assert reply_complete.ok is False


def test_create_500_raises_runtime_error_and_main_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke = _load()

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        return 500, {"detail": "Internal server error"}

    with pytest.raises(RuntimeError, match="(?i)create.*500"):
        smoke.run_smoke(
            base_url="http://127.0.0.1:8000",
            role="barb",
            question="Q",
            timeout_s=10.0,
            transport=fake_transport,
        )

    rc = smoke.main(["--base-url", "http://127.0.0.1:8000"], transport=fake_transport)
    assert rc == 2
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "500" in captured.err


def test_post_message_500_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    smoke = _load()

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and url.endswith("/api/v1/staff/threads"):
            return 201, {"id": "th-post-fail"}
        return 503, {"detail": "Service unavailable"}

    with pytest.raises(RuntimeError, match="(?i)post.*503"):
        smoke.run_smoke(
            base_url="http://127.0.0.1:8000",
            role="barb",
            question="Q",
            timeout_s=10.0,
            transport=fake_transport,
        )

    rc = smoke.main(["--base-url", "http://127.0.0.1:8000"], transport=fake_transport)
    assert rc == 2
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "503" in captured.err


def test_main_returns_0_on_all_pass_and_1_on_failed_check(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke = _load()

    # Case 1: All checks pass -> return 0
    passing_checks = [
        smoke.Check("reply complete", True, "delivery is 'complete'"),
        smoke.Check("reply non-empty", True, "24 chars"),
        smoke.Check("ack before reply", True, "user=1, ack=2, reply=3"),
    ]
    monkeypatch.setattr(smoke, "run_smoke", lambda *a, **kw: (passing_checks, 3.2))
    rc_pass = smoke.main(["--base-url", "http://127.0.0.1:8000"])
    assert rc_pass == 0
    out_pass = capsys.readouterr().out
    assert "PASS reply complete: delivery is 'complete'" in out_pass
    assert "PASS reply non-empty: 24 chars" in out_pass
    assert "PASS ack before reply: user=1, ack=2, reply=3" in out_pass
    assert "elapsed 3.2s" in out_pass

    # Case 2: One check fails -> return 1
    failing_checks = [
        smoke.Check("reply complete", True, "delivery is 'complete'"),
        smoke.Check("reply non-empty", False, "body is empty"),
        smoke.Check("ack before reply", True, "user=1, ack=2, reply=3"),
    ]
    monkeypatch.setattr(smoke, "run_smoke", lambda *a, **kw: (failing_checks, 1.8))
    rc_fail = smoke.main(["--base-url", "http://127.0.0.1:8000"])
    assert rc_fail == 1
    out_fail = capsys.readouterr().out
    assert "PASS reply complete: delivery is 'complete'" in out_fail
    assert "FAIL reply non-empty: body is empty" in out_fail
    assert "elapsed 1.8s" in out_fail


def test_bearer_header_sent_and_token_never_printed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke = _load()
    secret_token = "secret-token-xyz-12345"  # pragma: allowlist secret
    monkeypatch.setenv("STAFF_SMOKE_TOKEN", secret_token)

    recorded_headers: list[dict[str, str]] = []

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        recorded_headers.append(dict(headers))
        if method == "POST" and url.endswith("/api/v1/staff/threads"):
            return 201, {"id": "th-token-test"}
        if method == "POST" and "/messages" in url:
            assert headers.get("Idempotency-Key", "").startswith("smoke-")
            return 202, {}
        if method == "GET":
            return 200, {
                "thread": {"id": "th-token-test"},
                "messages": [
                    {"seq": 1, "author_kind": "user", "body_md": "Q"},
                    {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                    {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "Ans"},
                ],
            }
        return 404, {}

    rc = smoke.main(["--base-url", "http://127.0.0.1:8000"], transport=fake_transport)
    assert rc == 0
    assert len(recorded_headers) >= 3
    for h in recorded_headers:
        assert h.get("Authorization") == f"Bearer {secret_token}"
        assert h.get("X-Requested-With") == "XMLHttpRequest"
        assert h.get("Content-Type") == "application/json"

    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err


def test_preconditions_and_urlerror(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    smoke = _load()

    # Precondition validations
    with pytest.raises(ValueError, match="http"):
        smoke.run_smoke("ftp://invalid", "barb", "Q", 10.0)
    with pytest.raises(ValueError, match="role"):
        smoke.run_smoke("http://valid", "", "Q", 10.0)
    with pytest.raises(ValueError, match="timeout"):
        smoke.run_smoke("http://valid", "barb", "Q", 0.0)

    # Invalid base-url in main returns 2 and prints ERROR to stderr
    rc = smoke.main(["--base-url", "not-http-url"])
    assert rc == 2
    assert "ERROR" in capsys.readouterr().err

    # URLError in transport returns 2 and prints ERROR to stderr
    def error_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        raise urllib.error.URLError("Connection refused")

    rc_err = smoke.main(["--base-url", "http://127.0.0.1:8000"], transport=error_transport)
    assert rc_err == 2
    assert "ERROR" in capsys.readouterr().err


def test_check_memory_scenarios() -> None:
    smoke = _load()
    codeword = "SMOKE-AB12CD"

    # 1. Pass (case-insensitive match in second role reply)
    messages_pass: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "body_md": f"First message with {codeword}"},
        {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
        {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "First reply."},
        {"seq": 4, "author_kind": "user", "body_md": "What was the codeword?"},
        {"seq": 5, "author_kind": "system", "meta": {"acknowledgement": True}},
        {
            "seq": 6,
            "author_kind": "role",
            "delivery": "complete",
            "body_md": "The codeword you gave me was smoke-ab12cd.",
        },
    ]
    check_pass = smoke.check_memory(messages_pass, codeword)
    assert check_pass.name == "memory next turn"
    assert check_pass.ok is True
    assert check_pass.detail == "recalled SMOKE-AB12CD"

    # 2. Second reply missing (only one role message)
    messages_missing: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "body_md": "First"},
        {"seq": 2, "author_kind": "role", "delivery": "complete", "body_md": "First reply."},
    ]
    check_missing = smoke.check_memory(messages_missing, codeword)
    assert check_missing.name == "memory next turn"
    assert check_missing.ok is False
    assert check_missing.detail == "second reply missing"

    # 3. Delivery failed
    messages_failed: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "body_md": "First"},
        {"seq": 2, "author_kind": "role", "delivery": "complete", "body_md": "First reply."},
        {"seq": 3, "author_kind": "role", "delivery": "failed", "body_md": "Something failed."},
    ]
    check_failed = smoke.check_memory(messages_failed, codeword)
    assert check_failed.name == "memory next turn"
    assert check_failed.ok is False
    assert check_failed.detail == "delivery is 'failed'"

    # 4. Codeword absent
    messages_absent: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "user", "body_md": "First"},
        {"seq": 2, "author_kind": "role", "delivery": "complete", "body_md": "First reply."},
        {
            "seq": 3,
            "author_kind": "role",
            "delivery": "complete",
            "body_md": "I have no memory of any codeword provided earlier.",
        },
    ]
    check_absent = smoke.check_memory(messages_absent, codeword)
    assert check_absent.name == "memory next turn"
    assert check_absent.ok is False
    assert check_absent.detail == "codeword not in reply: I have no memory of any codeword provided earlier."

    # 5. Codeword absent with long body (>60 chars) truncated to first 60 chars
    long_reply = "A" * 80
    messages_long: list[dict[str, Any]] = [
        {"seq": 1, "author_kind": "role", "delivery": "complete", "body_md": "First reply."},
        {"seq": 2, "author_kind": "role", "delivery": "complete", "body_md": long_reply},
    ]
    check_long = smoke.check_memory(messages_long, codeword)
    assert check_long.ok is False
    assert check_long.detail == f"codeword not in reply: {'A' * 60}"


def test_run_smoke_memory_success_and_failure(capsys: pytest.CaptureFixture[str]) -> None:
    smoke = _load()

    def make_fake_transport(
        second_reply_body: str,
    ) -> tuple[Any, list[str]]:
        bodies: list[str] = []

        def fake_transport(
            method: str,
            url: str,
            body: dict[str, Any] | None,
            headers: dict[str, str],
        ) -> tuple[int, dict[str, Any]]:
            if method == "POST" and url.endswith("/api/v1/staff/threads"):
                return 201, {"id": "th-mem-test"}
            if method == "POST" and "/messages" in url:
                assert headers.get("Idempotency-Key", "").startswith("smoke-")
                if isinstance(body, dict) and "body" in body:
                    bodies.append(str(body["body"]))
                return 202, {}
            if method == "GET":
                if len(bodies) == 1:
                    return 200, {
                        "messages": [
                            {"seq": 1, "author_kind": "user", "body_md": bodies[0]},
                            {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                            {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "Turn 1 reply."},
                        ]
                    }
                return 200, {
                    "messages": [
                        {"seq": 1, "author_kind": "user", "body_md": bodies[0]},
                        {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                        {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "Turn 1 reply."},
                        {"seq": 4, "author_kind": "user", "body_md": bodies[1]},
                        {"seq": 5, "author_kind": "system", "meta": {"acknowledgement": True}},
                        {"seq": 6, "author_kind": "role", "delivery": "complete", "body_md": second_reply_body},
                    ]
                }
            return 404, {}

        return fake_transport, bodies

    # Subcase A: fake's second role reply contains the codeword -> all checks pass
    transport_pass, recorded_pass = make_fake_transport("The codeword is smoke-test01.")
    checks, _ = smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        role="barb",
        question="What is active?",
        timeout_s=30.0,
        memory=True,
        codeword="SMOKE-TEST01",
        transport=transport_pass,
    )
    assert len(recorded_pass) == 2
    assert "SMOKE-TEST01" in recorded_pass[0]
    assert (
        recorded_pass[1]
        == "Read-only check: what codeword did I give you in my first message? Reply with only the codeword."
    )
    assert len(checks) == 4
    assert [c.name for c in checks] == ["reply complete", "reply non-empty", "ack before reply", "memory next turn"]
    assert all(c.ok for c in checks)
    assert checks[3].detail == "recalled SMOKE-TEST01"

    # Subcase B: fake's second role reply does NOT contain codeword -> memory check fails, main-style result is 1
    transport_fail, recorded_fail = make_fake_transport("I do not recall any codeword.")
    checks_fail, _ = smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        role="barb",
        question="What is active?",
        timeout_s=30.0,
        memory=True,
        codeword="SMOKE-TEST01",
        transport=transport_fail,
    )
    assert len(recorded_fail) == 2
    mem_check = next(c for c in checks_fail if c.name == "memory next turn")
    assert mem_check.ok is False
    assert "codeword not in reply:" in mem_check.detail
    all_ok = all(c.ok for c in checks_fail)
    main_result = 0 if all_ok else 1
    assert main_result == 1

    # Verify main() CLI with --memory also returns 1
    rc = smoke.main(
        ["--base-url", "http://127.0.0.1:8000", "--memory"],
        transport=transport_fail,
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL memory next turn:" in captured.out


def test_run_smoke_memory_skipped_when_first_turn_fails() -> None:
    smoke = _load()
    recorded_bodies: list[str] = []

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and url.endswith("/api/v1/staff/threads"):
            return 201, {"id": "th-fail-first"}
        if method == "POST" and "/messages" in url:
            if isinstance(body, dict) and "body" in body:
                recorded_bodies.append(str(body["body"]))
            return 202, {}
        if method == "GET":
            return 200, {
                "messages": [
                    {"seq": 1, "author_kind": "user", "body_md": "Q"},
                    {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                    {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "   "},
                ]
            }
        return 404, {}

    checks, _ = smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        role="barb",
        question="What is active?",
        timeout_s=10.0,
        memory=True,
        codeword="SMOKE-TEST01",
        transport=fake_transport,
    )
    # Exactly ONE message posted
    assert len(recorded_bodies) == 1
    # First turn checks
    assert checks[0].name == "reply complete" and checks[0].ok is True
    assert checks[1].name == "reply non-empty" and checks[1].ok is False
    # Memory check skipped
    assert len(checks) == 4
    mem_check = checks[3]
    assert mem_check.name == "memory next turn"
    assert mem_check.ok is False
    assert mem_check.detail == "skipped: first turn failed"


def test_run_smoke_memory_false_posts_exactly_one_message() -> None:
    smoke = _load()
    recorded_bodies: list[str] = []

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and url.endswith("/api/v1/staff/threads"):
            return 201, {"id": "th-mem-false"}
        if method == "POST" and "/messages" in url:
            if isinstance(body, dict) and "body" in body:
                recorded_bodies.append(str(body["body"]))
            return 202, {}
        if method == "GET":
            return 200, {
                "messages": [
                    {"seq": 1, "author_kind": "user", "body_md": "Status?"},
                    {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                    {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "Nominal."},
                ]
            }
        return 404, {}

    checks, _ = smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        role="barb",
        question="Status?",
        timeout_s=10.0,
        memory=False,
        transport=fake_transport,
    )
    assert len(recorded_bodies) == 1
    assert recorded_bodies[0] == "Status?"
    assert len(checks) == 3
    assert all(c.ok for c in checks)
    assert [c.name for c in checks] == ["reply complete", "reply non-empty", "ack before reply"]


def test_run_smoke_memory_default_codeword_generation() -> None:
    smoke = _load()
    recorded_bodies: list[str] = []

    def fake_transport(
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and url.endswith("/api/v1/staff/threads"):
            return 201, {"id": "th-default-cw"}
        if method == "POST" and "/messages" in url:
            if isinstance(body, dict) and "body" in body:
                recorded_bodies.append(str(body["body"]))
            return 202, {}
        if method == "GET":
            cw = ""
            if recorded_bodies and "the codeword is " in recorded_bodies[0]:
                cw = recorded_bodies[0].split("the codeword is ")[-1].rstrip(".")
            return 200, {
                "messages": [
                    {"seq": 1, "author_kind": "user", "body_md": recorded_bodies[0]},
                    {"seq": 2, "author_kind": "system", "meta": {"acknowledgement": True}},
                    {"seq": 3, "author_kind": "role", "delivery": "complete", "body_md": "Turn 1 ok."},
                    {
                        "seq": 4,
                        "author_kind": "user",
                        "body_md": recorded_bodies[1] if len(recorded_bodies) > 1 else "",
                    },
                    {"seq": 5, "author_kind": "system", "meta": {"acknowledgement": True}},
                    {"seq": 6, "author_kind": "role", "delivery": "complete", "body_md": f"Codeword is {cw}"},
                ]
            }
        return 404, {}

    checks, _ = smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        role="barb",
        question="What is active?",
        timeout_s=10.0,
        memory=True,
        transport=fake_transport,
    )
    assert len(recorded_bodies) == 2
    assert "Also, for this thread only, the codeword is SMOKE-" in recorded_bodies[0]
    assert all(c.ok for c in checks)
