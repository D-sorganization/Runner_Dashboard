"""Root REVIEW.md exists and cites only real repo paths (#3652, RM #2565)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "REVIEW.md"
# A backticked token with a slash or a file suffix is treated as a repo path.
PATH = re.compile(r"`([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?|[A-Za-z0-9_-]+\.(?:md|py|yml))`")


def test_review_md_exists_at_repo_root() -> None:
    assert REVIEW.is_file()


def test_review_md_cites_only_existing_paths() -> None:
    cited = PATH.findall(REVIEW.read_text(encoding="utf-8"))
    assert cited, "REVIEW.md should cite the files its rules point at"
    missing = [p for p in cited if not (ROOT / p.rstrip("/")).exists()]
    assert missing == []
