from pathlib import Path

import pytest
from library.db import init_library_db
from library.verify import verify_content


@pytest.mark.parametrize(
    "token",
    [
        "[ref:desk:shared:w1:p19#sha256=notahash:p.7-8]",
        "[ref:shared:w1:p19@12345678:p.7-8]",
        "[ref:unknown-kind:missing]",
        "[ref:desk:shared:w1:p19#sha256=12345678:p.7-8",
    ],
)
def test_unrecognized_reference_fails_closed(tmp_path: Path, token: str) -> None:
    db = init_library_db(tmp_path / "library.sqlite")

    result = verify_content(db, f"A purportedly supported claim. {token}")

    assert result["verified"] is False
    assert result["citations"] == [{"citation": token, "verified": False, "status": "invalid_citation"}]


def test_plain_text_remains_verifiable(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    assert verify_content(db, "Ordinary prose without references.")["verified"] is True
