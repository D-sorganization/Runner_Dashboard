"""The vendored contract validator must match Repository_Management's source of truth.

The digest pin runs everywhere. The byte comparison runs only when a
Repository_Management checkout sits beside this repository (local hosts); CI skips
it because the dashboard never depends on a sibling repository.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_VENDORED = _ROOT / "backend" / "vuln_scan" / "contract.py"
_PINNED_SHA256 = "e3a4a439d7fe6b4c5d7351871f248753dd6f2e8aa6d8835c00ab2c22db391e79"  # pragma: allowlist secret
_SOURCE = Path("Repository_Management") / "shared_scripts" / "oss_scanner_contract.py"


def _lf_bytes(path: Path) -> bytes:
    # Windows checkouts may use CRLF (core.autocrlf); digests are over LF content.
    return path.read_bytes().replace(b"\r\n", b"\n")


def test_vendored_contract_matches_pinned_digest() -> None:
    digest = hashlib.sha256(_lf_bytes(_VENDORED)).hexdigest()
    assert digest == _PINNED_SHA256, "re-vendor from Repository_Management and update the pin"


def test_provenance_records_the_same_digest() -> None:
    provenance = (_VENDORED.parent / "PROVENANCE.md").read_text(encoding="utf-8")
    assert _PINNED_SHA256 in provenance


def test_vendored_contract_matches_sibling_source() -> None:
    candidates = [parent / _SOURCE for parent in (_ROOT.parent, _ROOT.parent.parent)]
    source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        pytest.skip("no Repository_Management checkout with the validator beside this repository")
    assert _lf_bytes(source) == _lf_bytes(_VENDORED)
