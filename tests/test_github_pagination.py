"""GitHub list pagination helper (#4278 PR1)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

from github_pagination import (  # noqa: E402
    decode_concatenated_json,
    flatten_paginated_items,
    parse_next_link,
)


def test_parse_next_link_three_page_chain() -> None:
    link = (
        '<https://api.github.com/repos/o/r/issues?page=2>; rel="next", '
        '<https://api.github.com/repos/o/r/issues?page=3>; rel="last"'
    )
    assert parse_next_link(link) == "https://api.github.com/repos/o/r/issues?page=2"
    assert parse_next_link("") is None
    assert parse_next_link('<https://api.github.com/x?page=9>; rel="last"') is None


def test_merge_page_chunks_includes_empty_terminal_page() -> None:
    chunks: list[Any] = [
        [{"number": 1}, {"number": 2}],
        [{"number": 3}],
        [],
    ]
    assert flatten_paginated_items(chunks) == [{"number": 1}, {"number": 2}, {"number": 3}]


def test_decode_concatenated_json_three_pages() -> None:
    text = '[{"n":1}][{"n":2}][]'
    chunks = decode_concatenated_json(text)
    assert len(chunks) == 3
    assert flatten_paginated_items(chunks) == [{"n": 1}, {"n": 2}]


def test_merge_page_chunks_check_runs_wrapper() -> None:
    chunks = [
        {"total_count": 2, "check_runs": [{"id": 1}, {"id": 2}]},
        {"total_count": 1, "check_runs": [{"id": 3}]},
        {"total_count": 0, "check_runs": []},
    ]
    assert flatten_paginated_items(chunks) == [{"id": 1}, {"id": 2}, {"id": 3}]


@pytest.mark.asyncio
async def test_gh_client_paginate_follows_link_through_three_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Link rel=next for pages 1–2; page 3 is an empty list (terminal)."""
    import gh_client

    pages = [
        (
            [{"id": "a"}, {"id": "b"}],
            '<https://api.github.com/repos/o/r/pulls?page=2&per_page=100>; rel="next"',
        ),
        (
            [{"id": "c"}],
            '<https://api.github.com/repos/o/r/pulls?page=3&per_page=100>; rel="next"',
        ),
        ([], ""),
    ]
    calls: list[str] = []

    class _Resp:
        def __init__(self, body: list[dict[str, str]], link: str) -> None:
            self.status_code = 200
            self.headers = {"link": link}
            self._body = body

        def json(self) -> list[dict[str, str]]:
            return self._body

    class _Client:
        async def request(self, method: str, path: str, **_kwargs: Any) -> _Resp:
            calls.append(path)
            body, link = pages.pop(0)
            return _Resp(body, link)

    gh_client.clear_token_cache()
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(gh_client, "_get_client", lambda: _Client())

    items = [item async for item in gh_client.paginate("/repos/o/r/pulls?state=open")]
    assert [row["id"] for row in items] == ["a", "b", "c"]
    assert len(calls) == 3
    gh_client.clear_token_cache()


def test_gh_cli_paginate_invokes_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    from github_pagination import gh_cli_paginate

    stdout = '[{"number": 10}][{"number": 11}][]'

    def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
        assert "--paginate" in cmd
        assert cmd[-1].endswith("per_page=100")

        class _Proc:
            returncode = 0
            stderr = ""

            def __init__(self) -> None:
                self.stdout = stdout

        return _Proc()

    monkeypatch.setattr("github_pagination.subprocess.run", _fake_run)
    items = gh_cli_paginate("/repos/o/r/issues?state=open")
    assert items == [{"number": 10}, {"number": 11}]
