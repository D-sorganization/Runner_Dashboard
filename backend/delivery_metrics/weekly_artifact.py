"""Optional idempotent weekly artifact hook (#2123, mirrors usage_report markers)."""

from __future__ import annotations

import os
import re
from typing import Final

_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
ENABLED_ENV: Final = "DELIVERY_METRICS_WEEKLY_ARTIFACT_ENABLED"


def weekly_summary_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def weekly_summary_marker(week: str) -> str:
    if not _WEEK_RE.match(week):
        raise ValueError(f"week must look like 2026-W40; got {week!r}")
    return f"<!-- delivery-metrics-week={week} -->"
