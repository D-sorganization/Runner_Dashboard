"""Shared fixtures for the Fleet API client tests (#1228).

``fake_api`` is a real ``http.server`` on an ephemeral loopback port that records every
request (method, path, query, headers, JSON body) and answers ``{"ok": true, "echo": ...}``
unless a canned response was registered with ``fake_api.respond(...)``.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

CLIENT_DIR = Path(__file__).resolve().parents[2] / "clients" / "fleet"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: Any


@dataclass
class FakeFleetAPI:
    url: str = ""
    requests: list[Recorded] = field(default_factory=list)
    canned: dict[tuple[str, str], tuple[int, Any]] = field(default_factory=dict)

    def respond(self, method: str, path: str, status: int, body: Any) -> None:
        self.canned[(method, path)] = (status, body)

    @property
    def last(self) -> Recorded:
        assert self.requests, "no request reached the fake API"
        return self.requests[-1]


def _handler(state: FakeFleetAPI) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            rec = Recorded(
                method=self.command,
                path=parsed.path,
                query=dict(urllib.parse.parse_qsl(parsed.query)),
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
            )
            state.requests.append(rec)
            status, payload = state.canned.get(
                (self.command, parsed.path),
                (200, {"ok": True, "echo": {"method": rec.method, "path": rec.path, "query": rec.query, "body": body}}),
            )
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _serve

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


@pytest.fixture
def fake_api() -> Iterator[FakeFleetAPI]:
    state = FakeFleetAPI()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _clean_fleet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FLEET_API_URL", "FLEET_API_TOKEN", "FLEET_API_TIMEOUT", "FLEET_AGENT", "FLEET_SESSION"):
        monkeypatch.delenv(name, raising=False)
