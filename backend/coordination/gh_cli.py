"""Small synchronous ``gh api`` helper for coordination evidence lookups."""

from __future__ import annotations

import json
import subprocess  # noqa: S404
from typing import Any

from coordination.rm_scripts import _rm_env
from github_pagination import gh_cli_paginate

GH_TIMEOUT_SECONDS = 20


def gh_api_json_all(path: str) -> list[Any]:
    """Paginated list fetch for coordination evidence (all pages, ``per_page=100``)."""
    return gh_cli_paginate(path, env=_rm_env(), timeout=GH_TIMEOUT_SECONDS)


def gh_api_json(path: str) -> Any:
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
        env=_rm_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"gh api {path} failed").strip()[:300])
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(f"gh api {path} returned empty stdout")
    return json.loads(out)
