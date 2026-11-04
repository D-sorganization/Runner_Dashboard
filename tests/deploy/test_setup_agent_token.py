"""Behavioral tests for deploy/setup-agent-token.sh (Runner_Dashboard#3895 slice 5).

Spins up a tiny stub HTTP server implementing the admin mint endpoint
(``POST /api/admin/principals/<id>/token``) so the script's happy path,
idempotency and error handling can be exercised without a live dashboard.
"""

from __future__ import annotations

import http.server
import json
import stat
import subprocess
import threading
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY / "setup-agent-token.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

FAKE_TOKEN = "svc_not_a_real_token_0123456789"


class _MintHandler(http.server.BaseHTTPRequestHandler):
    seen_bodies: list[dict[str, object]] = []
    status_to_return = 200
    response_body: dict[str, object] | None = None

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        assert self.headers.get("X-Requested-With") == "XMLHttpRequest"
        self.__class__.seen_bodies.append(json.loads(raw))
        self.send_response(self.__class__.status_to_return)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = self.__class__.response_body
        if body is not None:
            self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, *args: object) -> None:  # noqa: D102 - silence stdlib logging
        return


@pytest.fixture
def mint_server():
    _MintHandler.seen_bodies = []
    _MintHandler.status_to_return = 200
    _MintHandler.response_body = {"principal_id": "agent-codex", "token": FAKE_TOKEN}
    server = http.server.HTTPServer(("127.0.0.1", 0), _MintHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _MintHandler
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run([BASH, as_bash_path(SCRIPT), *args], capture_output=True, text=True)


def test_mints_and_writes_token_with_restricted_permissions(tmp_path: Path, mint_server) -> None:
    server, handler = mint_server
    identity_dir = tmp_path / "identity"

    res = _run(
        "--principal", "agent-codex",
        "--api-url", f"http://127.0.0.1:{server.server_port}",
        "--identity-dir", as_bash_path(identity_dir),
        "--expires-in-days", "30",
    )

    assert res.returncode == 0, f"script failed: {res.stderr}"
    assert FAKE_TOKEN not in res.stdout
    assert FAKE_TOKEN not in res.stderr

    token_file = identity_dir / "agent-tokens" / "agent-codex.token"
    assert token_file.read_text(encoding="utf-8") == FAKE_TOKEN
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

    assert len(handler.seen_bodies) == 1
    assert handler.seen_bodies[0]["expires_in_days"] == 30
    assert handler.seen_bodies[0]["name"]


def test_rerun_is_idempotent_without_remint(tmp_path: Path, mint_server) -> None:
    server, handler = mint_server
    identity_dir = tmp_path / "identity"

    first = _run(
        "--principal", "agent-codex",
        "--api-url", f"http://127.0.0.1:{server.server_port}",
        "--identity-dir", as_bash_path(identity_dir),
    )
    assert first.returncode == 0, first.stderr

    second = _run(
        "--principal", "agent-codex",
        "--api-url", f"http://127.0.0.1:{server.server_port}",
        "--identity-dir", as_bash_path(identity_dir),
    )
    assert second.returncode == 0, second.stderr
    assert "already present" in second.stdout
    assert len(handler.seen_bodies) == 1


def test_force_remints_even_when_token_file_exists(tmp_path: Path, mint_server) -> None:
    server, handler = mint_server
    identity_dir = tmp_path / "identity"

    _run(
        "--principal", "agent-codex",
        "--api-url", f"http://127.0.0.1:{server.server_port}",
        "--identity-dir", as_bash_path(identity_dir),
    )
    res = _run(
        "--principal", "agent-codex",
        "--api-url", f"http://127.0.0.1:{server.server_port}",
        "--identity-dir", as_bash_path(identity_dir),
        "--force",
    )

    assert res.returncode == 0, res.stderr
    assert len(handler.seen_bodies) == 2


def test_dry_run_makes_no_request_and_no_write(tmp_path: Path, mint_server) -> None:
    server, handler = mint_server
    identity_dir = tmp_path / "identity"

    res = _run(
        "--principal", "agent-codex",
        "--api-url", f"http://127.0.0.1:{server.server_port}",
        "--identity-dir", as_bash_path(identity_dir),
        "--dry-run",
    )

    assert res.returncode == 0, res.stderr
    assert "dry-run" in res.stdout
    assert handler.seen_bodies == []
    assert not (identity_dir / "agent-tokens" / "agent-codex.token").exists()


def test_http_error_fails_closed_without_writing(tmp_path: Path, mint_server) -> None:
    server, handler = mint_server
    handler.status_to_return = 403
    handler.response_body = {"error": "forbidden"}
    identity_dir = tmp_path / "identity"

    res = _run(
        "--principal", "agent-codex",
        "--api-url", f"http://127.0.0.1:{server.server_port}",
        "--identity-dir", as_bash_path(identity_dir),
    )

    assert res.returncode != 0
    assert not (identity_dir / "agent-tokens" / "agent-codex.token").exists()


def test_missing_principal_argument_is_rejected(tmp_path: Path, mint_server) -> None:
    server, _handler = mint_server
    res = _run("--api-url", f"http://127.0.0.1:{server.server_port}")
    assert res.returncode != 0
    assert "--principal" in res.stderr
