"""Unit tests for scripts/docker_pr_scope.py (#2764 detector fallback)."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from scripts.docker_pr_scope import (
    decide_run_docker_for_pr,
    fetch_pr_changed_files,
    should_run_docker_for_files,
)


def test_should_run_docker_for_backend_paths() -> None:
    assert should_run_docker_for_files(["backend/server.py"])


def test_should_skip_docs_only_pr() -> None:
    assert not should_run_docker_for_files(["docs/architecture.md", "README.md"])


def test_decide_run_docker_fail_open_on_http_403() -> None:
    error = mock.Mock()
    error.read.return_value = b'{"message":"Forbidden"}'

    def raise_403(*_args: object, **_kwargs: object) -> None:
        import urllib.error

        raise urllib.error.HTTPError(
            url="https://api.github.com",
            code=403,
            msg="Forbidden",
            hdrs=mock.Mock(),
            fp=error,
        )

    with mock.patch("urllib.request.urlopen", side_effect=raise_403):
        assert decide_run_docker_for_pr("org/repo", "1", "token") is True


def test_decide_run_docker_fail_open_on_network_error() -> None:
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=OSError("connection reset"),
    ):
        assert decide_run_docker_for_pr("org/repo", "1", "token") is True


def test_decide_run_docker_uses_file_list_when_api_succeeds() -> None:
    body = json.dumps([{"filename": "docs/readme.md"}]).encode()

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return body

    with mock.patch("urllib.request.urlopen", return_value=_Resp()):
        assert decide_run_docker_for_pr("org/repo", "1", "token") is False


def test_fetch_pr_changed_files_returns_none_on_malformed_json() -> None:
    with mock.patch(
        "urllib.request.urlopen",
        return_value=mock.Mock(read=lambda: b"not-json"),
    ):
        assert fetch_pr_changed_files("org/repo", "1", "token") is None
