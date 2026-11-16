"""Shared GitHub REST list pagination (``gh api --paginate`` and Link headers). Issue #4278."""

from __future__ import annotations

import json
import logging
import subprocess  # noqa: S404
from typing import Any

log = logging.getLogger("dashboard.github_pagination")

_LIST_KEYS = ("workflow_runs", "jobs", "runners", "items", "repositories", "check_runs")


def parse_next_link(link_header: str) -> str | None:
    """Return the ``rel=\"next\"`` URL from a GitHub ``Link`` response header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        url_part, *params = part.strip().split(";")
        url = url_part.strip().strip("<>")
        for param in params:
            if param.strip() == 'rel="next"':
                return url
    return None


def items_from_page(body: Any) -> list[Any]:
    """Normalize one GitHub list page (bare array or paginated object wrapper)."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in _LIST_KEYS:
            nested = body.get(key)
            if isinstance(nested, list):
                return nested
    return []


def decode_concatenated_json(text: str) -> list[Any]:
    """Decode ``gh api --paginate`` stdout (concatenated JSON documents)."""
    decoder = json.JSONDecoder()
    chunks: list[Any] = []
    pos = 0
    text = text or ""
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        page, pos = decoder.raw_decode(text, pos)
        chunks.append(page)
    return chunks


def merge_page_chunks(chunks: list[Any]) -> list[Any]:
    """Flatten page payloads into one list."""
    merged: list[Any] = []
    for chunk in chunks:
        merged.extend(items_from_page(chunk))
    return merged


def flatten_paginated_items(chunks: list[Any]) -> list[Any]:
    """Alias for merge_page_chunks (tests and CLI helpers)."""
    return merge_page_chunks(chunks)


def _with_per_page(path: str, *, per_page: int = 100) -> str:
    if "per_page=" in path:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}per_page={per_page}"


def gh_cli_paginate(
    path: str, *, env: dict[str, str] | None = None, timeout: int = 20, per_page: int = 100
) -> list[Any]:
    """Run ``gh api --paginate`` and return every item from the list endpoint."""
    path = _with_per_page(path, per_page=per_page)
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", "--paginate", path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"gh api --paginate {path} failed").strip()[:300])
    return merge_page_chunks(decode_concatenated_json(proc.stdout))


async def collect_paginated_list(path: str, *, per_page: int = 100) -> list[dict[str, Any]]:
    """Collect all items from ``gh_client.paginate`` (httpx + Link header)."""
    import gh_client as _gc

    return [item async for item in _gc.paginate(path, per_page=per_page)]
