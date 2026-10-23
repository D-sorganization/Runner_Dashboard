"""Canonical library citations and bounded passage snippets."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

_CITE_KEY = re.compile(
    r"^\[ref:(?P<node>[^:\]]+):(?P<source>[a-z0-9][a-z0-9-]{0,62}):w(?P<work>[1-9][0-9]*):"
    r"p(?P<passage>[1-9][0-9]*)#sha256=(?P<sha>[0-9a-fA-F]{8}):p\.(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)\]$"
)
_CITE_URI = re.compile(
    r"^lib://(?P<source>[a-z0-9][a-z0-9-]{0,62})/w(?P<work>[1-9][0-9]*)/"
    r"p(?P<passage>[1-9][0-9]*)@(?P<sha>[0-9a-fA-F]{8})#p(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)$"
)


def _parse_cite(cite: str) -> dict[str, Any]:
    match = _CITE_KEY.fullmatch(cite) or _CITE_URI.fullmatch(cite)
    if match is None:
        raise HTTPException(status_code=422, detail="cite must be a canonical library reference")
    values = match.groupdict()
    return {
        "node": values.get("node"),
        "source_id": values["source"],
        "work_id": int(values["work"]),
        "passage_id": int(values["passage"]),
        "sha_prefix": values["sha"].lower(),
        "page_start": int(values["start"]),
        "page_end": int(values["end"]),
    }


MAX_FETCH_TEXT_BYTES = 400  # Snippet cap; canonical citation is separate.


def _cap_fetch_text(value: Any) -> str:
    return str(value or "").encode("utf-8")[:MAX_FETCH_TEXT_BYTES].decode("utf-8", errors="ignore")
