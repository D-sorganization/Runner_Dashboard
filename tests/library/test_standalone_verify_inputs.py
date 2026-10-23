"""The standalone verifier can use explicitly supplied offline evidence."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from library import __main__ as library_cli
from library.db import init_library_db

pytestmark = pytest.mark.unit


def test_cli_uses_explicit_database_without_ambient_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = init_library_db(tmp_path / "selected" / "library.sqlite")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    document = tmp_path / "claim.md"
    document.write_text("No citations here.", encoding="utf-8")
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path / "ambient"))
    monkeypatch.setattr(sys, "argv", ["library", "verify", "--file", str(document), "--library-db", str(database)])

    assert library_cli.main() == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not (tmp_path / "ambient" / "library.sqlite").exists()


def test_cli_requires_explicit_database_to_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    document = tmp_path / "claim.md"
    document.write_text("No citations here.", encoding="utf-8")
    missing = tmp_path / "missing.sqlite"
    monkeypatch.setenv("LIBRARY_DATA_DIR", str(tmp_path / "ambient"))
    monkeypatch.setattr(sys, "argv", ["library", "verify", "--file", str(document), "--library-db", str(missing)])

    assert library_cli.main() == 2


def test_cli_explicit_knowledge_dir_disables_ambient_pack_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from library import codebase_refs

    database = init_library_db(tmp_path / "library.sqlite")
    document = tmp_path / "claim.md"
    document.write_text("A claim [ref:desk:codebase:Repo@12345678:src/module.py#L1-L1]", encoding="utf-8")
    explicit = tmp_path / "selected-knowledge"
    explicit.mkdir()
    ambient = tmp_path / "ambient-knowledge"
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(ambient))
    calls: list[tuple[Path | None, bool]] = []

    def unavailable(knowledge_dir: Path | None = None, *, allow_ambient: bool = True):
        calls.append((knowledge_dir, allow_ambient))
        raise FileNotFoundError("fixture pack absent")

    monkeypatch.setattr(codebase_refs, "_context", unavailable)
    monkeypatch.setattr(
        sys,
        "argv",
        ["library", "verify", "--file", str(document), "--library-db", str(database), "--knowledge-dir", str(explicit)],
    )

    assert library_cli.main() == 1
    assert json.loads(capsys.readouterr().out)["citations"][0]["status"] == "codebase_unavailable"
    assert calls == [(explicit, False)]
    assert not ambient.exists()


def test_cli_local_only_never_calls_fleet_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from library import verify

    database = init_library_db(tmp_path / "library.sqlite")
    document = tmp_path / "claim.md"
    document.write_text("Claim [ref:peer:source:w1:p2#sha256=abcdef12:p.1-1]", encoding="utf-8")
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "HOSTNAME", "desk")
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {"peer": "https://peer.example.invalid"})
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fixture-only-token")
    monkeypatch.setattr(verify, "_fetch_peer_response", lambda *args, **kwargs: pytest.fail("fleet request attempted"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["library", "verify", "--file", str(document), "--library-db", str(database), "--local-only"],
    )

    assert library_cli.main() == 1
    assert json.loads(capsys.readouterr().out)["citations"][0]["status"] == "node_mismatch"
