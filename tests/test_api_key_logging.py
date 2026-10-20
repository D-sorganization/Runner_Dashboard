"""API-key startup diagnostics must never disclose generated credentials."""

from __future__ import annotations

import logging
import os
import secrets as test_secrets
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402


def _contains_secret(records: list[logging.LogRecord], secret: str) -> bool:
    return any(secret in record.getMessage() or secret in repr(record.args) for record in records)


class _LogCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _capture_dashboard_logs() -> Iterator[list[logging.LogRecord]]:
    logger = logging.getLogger("dashboard")
    collector = _LogCollector()
    original_handlers = logger.handlers[:]
    original_propagate = logger.propagate
    logger.handlers = [collector]
    logger.propagate = False
    try:
        yield collector.records
    finally:
        logger.handlers = original_handlers
        logger.propagate = original_propagate


def test_generated_key_is_returned_persisted_and_never_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-" + test_secrets.token_urlsafe(24)
    key_file = tmp_path / "runner-dashboard" / "api_key.txt"
    chmod_modes: list[int] = []
    original_chmod = Path.chmod

    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(server.secrets, "token_urlsafe", lambda _size: secret)

    def record_chmod(path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        if path == key_file:
            chmod_modes.append(mode)
        original_chmod(path, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "chmod", record_chmod)

    with _capture_dashboard_logs() as records:
        returned = server._load_or_generate_api_key()

    returned_as_expected = returned == secret
    persisted_as_expected = key_file.read_text(encoding="utf-8") == secret
    mode_as_expected = chmod_modes == [0o600]
    useful_diagnostic = any("Dashboard request-auth value saved" in record.getMessage() for record in records)
    credential_logged = _contains_secret(records, secret)

    assert returned_as_expected, "generated API key was not returned"
    assert persisted_as_expected, "generated API key was not persisted"
    assert mode_as_expected, "generated API key file mode changed"
    assert useful_diagnostic, "successful key persistence should leave a useful diagnostic"
    assert not credential_logged, "API credential appeared in startup logs"


def test_persistence_failure_keeps_useful_secret_free_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "test-" + test_secrets.token_urlsafe(24)
    key_directory = tmp_path / "runner-dashboard"

    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(server.secrets, "token_urlsafe", lambda _size: secret)
    original_mkdir = Path.mkdir

    def fail_key_directory(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path == key_directory:
            raise OSError("simulated permission failure")
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", fail_key_directory)

    with _capture_dashboard_logs() as records:
        returned = server._load_or_generate_api_key()

    returned_as_expected = returned == secret
    not_persisted = not (key_directory / "api_key.txt").exists()
    useful_diagnostic = any(
        "Dashboard request-auth value could not be saved" in record.getMessage()
        and "simulated permission failure" in record.getMessage()
        for record in records
    )
    credential_logged = _contains_secret(records, secret)

    assert returned_as_expected, "generated API key was not returned after persistence failure"
    assert not_persisted, "API key unexpectedly persisted despite simulated failure"
    assert useful_diagnostic, "API-key persistence failure lost useful diagnostic context"
    assert not credential_logged, "API credential appeared in failure logs"
