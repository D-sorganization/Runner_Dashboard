"""Read-only, local verification of Markdown file links and heading anchors."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\((?P<url>[^)]+)\)")
_MAX_CONTENT_CHARS = 1_000_000
_MAX_TARGET_BYTES = 2_000_000


def _anchors(markdown: str) -> set[str]:
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", markdown, re.MULTILINE)
    return {re.sub(r"[^\w-]", "", re.sub(r"\s+", "-", re.sub(r"[`*_]", "", heading.lower()))) for heading in headings}


def _link_result(url: str, verified: bool, status: str) -> dict[str, Any]:
    return {"url": url[:2_000], "verified": verified, "status": status}


def verify_links(
    content: str,
    *,
    repo_root: Path | None,
    document_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Check local Markdown links without opening or requiring a citation database.

    Relative paths in a document resolve from its directory. If no document path
    is supplied, links resolve from the repository root, suitable for PR bodies.
    External links remain explicitly unchecked and no network request is made.
    """
    if not isinstance(content, str) or len(content) > _MAX_CONTENT_CHARS:
        raise ValueError("content must be text of at most 1000000 characters")
    if repo_root is None:
        root = None
        document = None
        if document_path is not None:
            raise ValueError("document path requires a repository root")
    else:
        try:
            root = Path(repo_root).resolve(strict=True)
        except OSError as exc:
            raise ValueError("repository root is unavailable") from exc
        if not root.is_dir():
            raise ValueError("repository root must be a directory")
        document = None
        if document_path is not None:
            try:
                document = Path(document_path).resolve(strict=True)
            except OSError as exc:
                raise ValueError("document path is unavailable") from exc
            if root not in document.parents or not document.is_file():
                raise ValueError("document path must be a file inside repository root")

    results: list[dict[str, Any]] = []
    for match in _LINK.finditer(content):
        url = match.group("url").strip()
        if url.startswith(("https://", "http://", "mailto:")):
            results.append(_link_result(url, True, "external_unchecked"))
            continue
        path_text, _, anchor = url.partition("#")
        if root is None:
            results.append(_link_result(url, False, "broken_link"))
            continue
        if not path_text:
            if document is None:
                results.append(_link_result(url, False, "broken_link"))
                continue
            raw_target = document
        else:
            base = document.parent if document is not None else root
            raw_target = base / unquote(path_text)
        try:
            target = raw_target.resolve(strict=True)
        except (OSError, RuntimeError):
            results.append(_link_result(url, False, "broken_link"))
            continue
        if root not in target.parents or not target.is_file():
            results.append(_link_result(url, False, "broken_link"))
            continue
        if anchor:
            try:
                if target.stat().st_size > _MAX_TARGET_BYTES:
                    raise ValueError("Markdown target exceeds the verification limit")
                markdown = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError, ValueError):
                results.append(_link_result(url, False, "broken_link"))
                continue
            if unquote(anchor) not in _anchors(markdown):
                results.append(_link_result(url, False, "broken_link"))
                continue
        results.append(_link_result(url, True, "verified"))
    return results
