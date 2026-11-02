"""Small synchronous ``gh api`` helper for coordination evidence lookups."""

from __future__ import annotations

import json
import subprocess  # noqa: S404
from typing import Any

GH_TIMEOUT_SECONDS = 20


def gh_api_json(path: str) -> Any:
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"gh api {path} failed").strip()[:300])
    return json.loads(proc.stdout) if proc.stdout.strip() else {}
